import hashlib
import os
import re
import sys

from plan_evidence import EvidenceError, canonical_record
from verify_promotion import (
    GateError,
    GitHubApi,
    list_run_artifacts,
    parse_positive_int,
    repository_file,
    validate_full_sha,
    validate_reviewed_pr,
)


WORKFLOW_PATH = ".github/workflows/staging-terraform-plan.yml"
PLAN_REPOSITORY = "Celoria-Industries/infrastructure-as-code"
PLAN_ARTIFACT_NAME = "staging-terraform-plan"
EVIDENCE_ARTIFACT_NAME = "staging-terraform-evidence"
PLAN_ROOT_PATTERN = re.compile(
    r"stacks/[a-z0-9][a-z0-9-]*/staging/[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*"
)


def require(condition, message):
    if not condition:
        raise GateError(message)


def required_env(name):
    value = os.environ.get(name, "")
    require(bool(value), f"{name} must be configured")
    return value


def main():
    repository = required_env("GITHUB_REPOSITORY")
    require(repository == PLAN_REPOSITORY,
            f"Terraform plan producer is restricted to {PLAN_REPOSITORY}")
    source_pr_number = parse_positive_int(required_env("SOURCE_PR_NUMBER"), "SOURCE_PR_NUMBER")
    source_pr, source_head = validate_reviewed_pr(
        GitHubApi(), repository, source_pr_number
    )
    source_merge = validate_full_sha(source_pr.get("merge_commit_sha"), "source PR merge SHA")
    expected_head = validate_full_sha(required_env("SOURCE_PR_HEAD_SHA"), "SOURCE_PR_HEAD_SHA")
    require(source_head == expected_head, "Source PR head changed after plan creation")
    require(source_merge == required_env("SOURCE_PR_MERGE_SHA"),
            "Source PR merge commit changed after plan creation")

    run_id = parse_positive_int(required_env("GITHUB_RUN_ID"), "GITHUB_RUN_ID")
    run_attempt = parse_positive_int(required_env("GITHUB_RUN_ATTEMPT"), "GITHUB_RUN_ATTEMPT")
    run_head = validate_full_sha(required_env("GITHUB_SHA"), "GITHUB_SHA")
    trusted_workflow = required_env("STAGING_TRUSTED_PLAN_WORKFLOW")
    require(
        re.fullmatch(
            r"Celoria-Industries/\.github/\.github/workflows/staging-terraform-plan\.yml@[0-9a-f]{40}",
            trusted_workflow,
        ) is not None,
        "STAGING_TRUSTED_PLAN_WORKFLOW must be a full immutable central workflow ref",
    )

    api = GitHubApi()
    run = api.get(f"/repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}")
    require(run.get("status") == "in_progress",
            "Terraform plan producer run is no longer in progress")
    require(run.get("event") == "workflow_dispatch" and run.get("head_branch") == "main",
            "Terraform plan producer must run from workflow_dispatch on main")
    require(run.get("head_sha") == run_head, "Terraform plan producer run commit changed")
    require(run.get("run_attempt") == run_attempt, "Terraform plan run attempt changed")
    require(run.get("path", "").split("@", 1)[0] == WORKFLOW_PATH,
            "Terraform plan producer used an unexpected caller workflow")
    relation = api.get(f"/repos/{repository}/compare/{source_merge}...{run_head}")
    require(relation.get("status") in ("identical", "ahead"),
            "Source PR merge commit is not an ancestor of the plan producer run")
    wrapper = repository_file(api, repository, WORKFLOW_PATH, run_head)
    uses = re.findall(r"(?m)^\s+uses:\s*([^\s#]+)\s*(?:#.*)?$", wrapper)
    has_steps_or_commands = re.search(r"(?m)^[ \t]*(?:steps|run)\s*:", wrapper) is not None
    require(uses == [trusted_workflow] and not has_steps_or_commands,
            "Terraform source workflow must only call the pinned reusable plan workflow")

    artifact_id = parse_positive_int(required_env("PLAN_ARTIFACT_ID"), "PLAN_ARTIFACT_ID")
    artifact_digest = required_env("PLAN_ARTIFACT_DIGEST")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_digest) is not None,
            "PLAN_ARTIFACT_DIGEST must be a SHA-256 digest")
    run_artifacts = list_run_artifacts(api, repository, run_id)
    require(
        len([item for item in run_artifacts if item.get("name") == PLAN_ARTIFACT_NAME]) == 1,
        "Current producer run must contain exactly one Terraform plan artifact",
    )
    require(
        not any(item.get("name") == EVIDENCE_ARTIFACT_NAME for item in run_artifacts),
        "Current producer run already contains signed Terraform evidence",
    )
    artifact = api.get(f"/repos/{repository}/actions/artifacts/{artifact_id}")
    require(artifact.get("id") == artifact_id, "Plan artifact ID does not match the GitHub artifact")
    require(artifact.get("name") == PLAN_ARTIFACT_NAME, "Unexpected Terraform plan artifact name")
    require(artifact.get("digest") == artifact_digest, "Plan artifact digest does not match GitHub")
    require(artifact.get("expired") is False, "Terraform plan artifact has expired")
    require((artifact.get("workflow_run") or {}).get("id") == run_id,
            "Terraform plan artifact belongs to another workflow run")

    plan_path = required_env("PLAN_ARTIFACT_PATH")
    require(os.path.isfile(plan_path), "Saved Terraform plan artifact does not contain tfplan")
    with open(plan_path, "rb") as plan_file:
        plan_digest = hashlib.sha256(plan_file.read()).hexdigest()

    account_id = required_env("STAGING_PLAN_TARGET_ACCOUNT_ID")
    region = required_env("STAGING_PLAN_TARGET_REGION")
    terraform_root = required_env("STAGING_PLAN_ROOT")
    key_arn = required_env("STAGING_PLAN_SIGNING_KEY_ARN")
    require(re.fullmatch(r"[0-9]{12}", account_id) is not None,
            "STAGING_PLAN_TARGET_ACCOUNT_ID must be a 12-digit AWS account ID")
    require(re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]", region) is not None,
            "STAGING_PLAN_TARGET_REGION is invalid")
    require(PLAN_ROOT_PATTERN.fullmatch(terraform_root) is not None,
            "STAGING_PLAN_ROOT must be a staging stack path")
    require(re.fullmatch(r"arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[0-9a-f-]{36}", key_arn) is not None,
            "STAGING_PLAN_SIGNING_KEY_ARN must be an immutable KMS key ARN")

    record = {
        "schema_version": 1,
        "target": "staging",
        "source_repository": repository,
        "source_commit": source_merge,
        "source_pull_request_number": source_pr_number,
        "source_pull_request_head_sha": source_head,
        "source_pull_request_merge_sha": source_merge,
        "producer_run_id": run_id,
        "producer_run_attempt": run_attempt,
        "producer_run_head_sha": run_head,
        "producer_workflow_ref": trusted_workflow,
        "plan_artifact_id": artifact_id,
        "plan_artifact_name": PLAN_ARTIFACT_NAME,
        "plan_artifact_digest": artifact_digest,
        "plan_sha256": f"sha256:{plan_digest}",
        "target_account_id": account_id,
        "target_region": region,
        "terraform_root": terraform_root,
        "signing_key_arn": key_arn,
        "signing_algorithm": "RSASSA_PSS_SHA_256",
    }
    signed_bytes = canonical_record(record)
    output_directory = required_env("EVIDENCE_OUTPUT_DIRECTORY")
    os.makedirs(output_directory, mode=0o700, exist_ok=True)
    with open(os.path.join(output_directory, "evidence.json"), "wb") as output:
        output.write(signed_bytes)
    print(f"Prepared canonical evidence for plan artifact {artifact_id} in run {run_id}.")


if __name__ == "__main__":
    try:
        main()
    except (GateError, EvidenceError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
