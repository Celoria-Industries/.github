import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from plan_evidence import EvidenceError, strict_record, verify_plan_bytes, verify_plan_evidence
from solo_approval import SoloApprovalError, verify_comment


MANIFEST_PATH = ".github/staging-promotion.json"
PLAN_WORKFLOW_PATH = ".github/workflows/staging-terraform-plan.yml"
PLAN_ARTIFACT_NAME = "staging-terraform-plan"
EVIDENCE_ARTIFACT_NAME = "staging-terraform-evidence"
PLAN_REPOSITORY = "Celoria-Industries/infrastructure-as-code"


class GateError(Exception):
    pass


def fail(message):
    raise GateError(message)


def require(condition, message):
    if not condition:
        fail(message)


def strict_json(data, source):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"{source} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        return json.loads(data, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(f"{source} is not valid UTF-8 JSON") from error


class GitHubApi:
    def __init__(self):
        self.token = os.environ.get("GH_TOKEN", "")
        self.base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        require(bool(self.token), "GitHub API token is unavailable")
        self.base_origin = urllib.parse.urlsplit(self.base)[:2]

    def _request(self, url, authenticated=True):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Celoria-Staging-Promotion-Gate",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, response, code, message, headers, new_url):
                return None

        try:
            opener = urllib.request.build_opener(NoRedirect)
            return opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            if authenticated and error.code in (301, 302, 303, 307, 308):
                location = error.headers.get("Location")
                require(location is not None, "Artifact download redirect has no location")
                target = urllib.parse.urlsplit(location)
                require(target.scheme == "https" and target.hostname, "GitHub API redirect is not HTTPS")
                require(target[:2] == self.base_origin, "GitHub API redirected to an unexpected host")
                return opener.open(
                    urllib.request.Request(location, headers=headers),
                    timeout=30,
                )
            detail = error.read(2048).decode("utf-8", errors="replace")
            fail(f"GitHub API request failed ({error.code}): {detail}")
        except (urllib.error.URLError, TimeoutError) as error:
            fail(f"GitHub API request failed: {error}")

    def get(self, path):
        url = urllib.parse.urljoin(self.base + "/", path.lstrip("/"))
        require(urllib.parse.urlsplit(url)[:2] == self.base_origin, "GitHub API URL escaped its configured host")
        with self._request(url) as response:
            return strict_json(response.read(), "GitHub API response")

    def list(self, path):
        result = []
        for page in range(1, 101):
            separator = "&" if "?" in path else "?"
            items = self.get(f"{path}{separator}per_page=100&page={page}")
            require(isinstance(items, list), "GitHub API list response was not an array")
            result.extend(items)
            if len(items) < 100:
                return result
        fail("GitHub API list exceeded the 10,000-item safety limit")

def parse_positive_int(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError):
        fail(f"{label} must be a positive integer")
    require(number > 0, f"{label} must be a positive integer")
    return number


def validate_full_sha(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None,
            f"{label} must be a lowercase 40-character commit SHA")
    return value


def validate_digest(value, label):
    require(isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None,
            f"{label} must be a sha256 digest")
    return value


def validate_repo(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is not None,
            f"{label} must be an owner/repository name")
    return value


def approval_mode():
    mode = os.environ.get("STAGING_APPROVAL_MODE", "") or "independent"
    require(mode in ("independent", "solo"), "Invalid staging approval mode")
    return mode


def verify_solo_pr(api, repository, number, head_sha, purpose, extras=None):
    repo_id = parse_positive_int(os.environ.get("GITHUB_REPOSITORY_ID"), "GITHUB_REPOSITORY_ID")
    author_id = parse_positive_int(os.environ.get("STAGING_SOLO_AUTHORIZER_ID"), "STAGING_SOLO_AUTHORIZER_ID")
    max_age = parse_positive_int(os.environ.get("STAGING_SOLO_APPROVAL_MAX_AGE_SECONDS"), "STAGING_SOLO_APPROVAL_MAX_AGE_SECONDS")
    comment_id = parse_positive_int(
        os.environ.get(f"STAGING_SOLO_{purpose.upper()}_COMMENT_ID"), f"{purpose} authorization comment ID"
    )
    account = os.environ.get("STAGING_SOLO_TARGET_ACCOUNT_ID", "")
    region = os.environ.get("STAGING_SOLO_TARGET_REGION", "")
    require(re.fullmatch(r"[0-9]{12}", account) is not None, "Invalid solo staging target account")
    require(re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]", region) is not None, "Invalid solo staging target region")
    expected = {
        "schema_version": 1,
        "type": purpose,
        "repository_id": repo_id,
        "pull_request": number,
        "head_sha": head_sha,
        "target": "staging",
        "account_id": account,
        "region": region,
    }
    if extras:
        expected.update(extras)
    try:
        return verify_comment(api, repository, repo_id, comment_id, author_id, max_age, expected)
    except SoloApprovalError as error:
        raise GateError(str(error)) from error


def validate_reviewed_pr(api, repository, number, required_head=None, promotion=False):
    pr = api.get(f"/repos/{repository}/pulls/{number}")
    require(pr.get("state") == "closed" and pr.get("merged_at"), f"PR #{number} is not merged")
    require(pr.get("base", {}).get("ref") == "main", f"PR #{number} must target main")
    require(pr.get("base", {}).get("repo", {}).get("full_name") == repository,
            f"PR #{number} targets a different repository")
    require(pr.get("head", {}).get("repo", {}).get("full_name") == repository,
            f"PR #{number} must come from a branch in {repository}")
    head_sha = validate_full_sha(pr.get("head", {}).get("sha"), f"PR #{number} head")
    require(required_head is None or head_sha == required_head,
            f"PR #{number} head does not match the requested source commit")

    reviews = api.list(f"/repos/{repository}/pulls/{number}/reviews")
    latest = {}
    for review in reviews:
        user = review.get("user") or {}
        submitted_at = review.get("submitted_at")
        if not submitted_at or not user.get("login"):
            continue
        login = user["login"].lower()
        if login not in latest or submitted_at >= latest[login].get("submitted_at", ""):
            latest[login] = review
    approved = any(
        review.get("state") == "APPROVED"
        and review.get("commit_id") == head_sha
        and (review.get("user") or {}).get("type") == "User"
        and not (review.get("user") or {}).get("login", "").lower().endswith("[bot]")
        and (review.get("user") or {}).get("login", "").lower()
        != (pr.get("user") or {}).get("login", "").lower()
        for review in latest.values()
    )
    if approval_mode() == "independent":
        require(approved, f"PR #{number} lacks a current human approval on its exact head")
    elif not promotion:
        verify_solo_pr(api, repository, number, head_sha, "source")

    check_runs = []
    for page in range(1, 101):
        checks = api.get(
            f"/repos/{repository}/commits/{head_sha}/check-runs"
            f"?filter=latest&per_page=100&page={page}"
        )
        check_runs.extend(checks.get("check_runs", []))
        if len(check_runs) >= checks.get("total_count", 0):
            break
    require(len(check_runs) < 10000, f"PR #{number} has too many check runs to verify safely")
    require(bool(check_runs), f"PR #{number} has no check-run evidence")
    current_reviews = [
        review for review in latest.values()
        if review.get("commit_id") == head_sha
    ]
    require(not any(review.get("state") in ("CHANGES_REQUESTED", "DISMISSED") for review in current_reviews),
            f"PR #{number} has unresolved change requests or dismissed approvals")
    require(all(
        check.get("status") == "completed"
        and check.get("conclusion") in ("success", "neutral", "skipped")
        for check in check_runs
    ), f"PR #{number} has failed, incomplete, or cancelled checks")

    if promotion:
        require(pr.get("changed_files") == 1, "Promotion PR must change only the staging-promotion manifest")
        files = api.get(f"/repos/{repository}/pulls/{number}/files?per_page=2")
        require(len(files) == 1 and files[0].get("filename") == MANIFEST_PATH,
                f"Promotion PR must change only {MANIFEST_PATH}")
    return pr, head_sha


def require_object(value, required_keys, label):
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == set(required_keys), f"{label} must contain exactly: {', '.join(required_keys)}")
    return value


def validate_image_manifest(manifest, repository, trusted_workflow):
    require_object(manifest, ("schema_version", "target", "kind", "source", "destination"), "manifest")
    require(manifest["schema_version"] == 1, "Unsupported promotion manifest schema_version")
    require(manifest["target"] == "staging" and manifest["kind"] == "image",
            "Image promotion manifest must target staging")
    source = require_object(
        manifest["source"],
        (
            "repository",
            "commit",
            "workflow_run_id",
            "workflow_run_attempt",
            "image_repository",
            "image_digest",
            "producer_workflow_ref",
        ),
        "source",
    )
    require(validate_repo(source["repository"], "source.repository") == repository,
            "Image source repository must match the caller repository")
    validate_full_sha(source["commit"], "source.commit")
    source["workflow_run_id"] = parse_positive_int(source["workflow_run_id"], "source.workflow_run_id")
    source["workflow_run_attempt"] = parse_positive_int(
        source["workflow_run_attempt"], "source.workflow_run_attempt"
    )
    validate_digest(source["image_digest"], "source.image_digest")
    require(
        isinstance(trusted_workflow, str)
        and re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/container-image\.yml@[0-9a-f]{40}",
            trusted_workflow,
        ) is not None,
        "STAGING_TRUSTED_IMAGE_WORKFLOW must pin container-image.yml to a full commit SHA",
    )
    require(source["producer_workflow_ref"] == trusted_workflow,
            "Image producer does not match the admin-configured immutable workflow")
    validate_image_name(source["image_repository"], "source.image_repository")
    destination = require_object(manifest["destination"], ("image_repository",), "destination")
    validate_image_name(destination["image_repository"], "destination.image_repository")
    require(destination["image_repository"] == source["image_repository"],
            "Staging destination repository must match the Dev source repository")
    return source, destination


def validate_image_name(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9]+([._/-][a-z0-9]+)*", value) is not None,
            f"{label} is not a valid ECR repository name")


def validate_promotion_trigger(event_name, ref):
    require(
        ref == "refs/heads/main" and event_name in ("push", "workflow_dispatch"),
        "Staging promotion may run only from a push or manual dispatch on main",
    )


def validate_supported_kind(kind):
    require(kind in ("image", "terraform"), "Promotion manifest kind must be image or terraform")


def repository_file(api, repository, path, ref):
    encoded_path = urllib.parse.quote(path, safe="/")
    response = api.get(f"/repos/{repository}/contents/{encoded_path}?ref={ref}")
    require(response.get("encoding") == "base64" and isinstance(response.get("content"), str),
            f"{path} could not be read as base64")
    try:
        return base64.b64decode(response["content"].replace("\n", ""), validate=True).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise GateError(f"{path} contains invalid base64 or UTF-8 content") from error


def validate_source_image_run(api, repository, source, trusted_workflow):
    source_pr = validate_reviewed_source_commit(api, repository, source["commit"])
    run = api.get(
        f'/repos/{repository}/actions/runs/{source["workflow_run_id"]}'
        f'/attempts/{source["workflow_run_attempt"]}'
    )
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "Source image workflow run did not complete successfully")
    require(run.get("event") == "push" and run.get("head_branch") == "main",
            "Source image workflow must be a push to main")
    require(run.get("head_sha") == source["commit"], "Source image workflow commit does not match the manifest")
    require(run.get("run_attempt") == source["workflow_run_attempt"], "Source image run attempt does not match")
    caller_workflow_path = run.get("path", "").split("@", 1)[0]
    require(
        re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.ya?ml", caller_workflow_path)
        is not None,
        "Source image run has an invalid caller workflow path",
    )
    require(source["producer_workflow_ref"] == trusted_workflow,
            "Image producer does not match the admin-configured immutable workflow")
    wrapper = repository_file(api, repository, caller_workflow_path, source["commit"])
    uses = re.findall(r"(?m)^\s+uses:\s*([^\s#]+)\s*(?:#.*)?$", wrapper)
    has_run_step = re.search(r"(?m)^[ \t]*(?:-\s*)?run\s*:", wrapper) is not None
    require(uses == [trusted_workflow] and not has_run_step,
            "Source workflow must only call the pinned reusable image workflow, with no run steps")
    return source_pr


def validate_reviewed_source_commit(api, repository, source_commit):
    validate_full_sha(source_commit, "Image source commit")
    pull_requests = api.list(f"/repos/{repository}/commits/{source_commit}/pulls")
    matches = [
        pr for pr in pull_requests
        if pr.get("merge_commit_sha") == source_commit
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("base") or {}).get("repo", {}).get("full_name") == repository
        and (pr.get("head") or {}).get("repo", {}).get("full_name") == repository
    ]
    require(len(matches) == 1,
            "Image source commit must belong to exactly one merged PR targeting main")
    pr, _ = validate_reviewed_pr(api, repository, matches[0].get("number"))
    require(pr.get("merge_commit_sha") == source_commit,
            "Image source PR merge commit does not match the producer commit")
    return pr


def validate_terraform_manifest(manifest, repository, trusted_workflow, expected):
    require(repository == PLAN_REPOSITORY,
            f"Terraform staging promotion is restricted to {PLAN_REPOSITORY}")
    require_object(manifest, ("schema_version", "target", "kind", "source"), "manifest")
    require(manifest["schema_version"] == 1, "Unsupported promotion manifest schema_version")
    require(manifest["target"] == "staging" and manifest["kind"] == "terraform",
            "Terraform promotion manifest must target staging")
    source = require_object(
        manifest["source"],
        (
            "repository",
            "commit",
            "pull_request_number",
            "pull_request_head_sha",
            "pull_request_merge_sha",
            "workflow_run_id",
            "workflow_run_attempt",
            "workflow_run_head_sha",
            "producer_workflow_ref",
            "plan_artifact_id",
            "plan_artifact_name",
            "plan_artifact_digest",
            "plan_sha256",
            "evidence_artifact_id",
            "evidence_artifact_name",
            "evidence_artifact_digest",
            "target_account_id",
            "target_region",
            "terraform_root",
            "signing_key_arn",
            "signing_algorithm",
        ),
        "Terraform manifest source",
    )
    require(validate_repo(source["repository"], "source.repository") == repository,
            "Terraform source repository must match the caller repository")
    for key in ("commit", "pull_request_head_sha", "pull_request_merge_sha", "workflow_run_head_sha"):
        validate_full_sha(source[key], f"source.{key}")
    require(source["commit"] == source["pull_request_merge_sha"],
            "Terraform source commit must be the reviewed PR merge commit")
    for key in ("pull_request_number", "workflow_run_id", "workflow_run_attempt",
                "plan_artifact_id", "evidence_artifact_id"):
        source[key] = parse_positive_int(source[key], f"source.{key}")
    require(source["plan_artifact_id"] != source["evidence_artifact_id"],
            "Terraform plan and evidence must be separate artifacts")
    require(source["plan_artifact_name"] == PLAN_ARTIFACT_NAME,
            "Terraform plan artifact name is invalid")
    require(source["evidence_artifact_name"] == EVIDENCE_ARTIFACT_NAME,
            "Terraform evidence artifact name is invalid")
    validate_digest(source["plan_artifact_digest"], "source.plan_artifact_digest")
    validate_digest(source["plan_sha256"], "source.plan_sha256")
    validate_digest(source["evidence_artifact_digest"], "source.evidence_artifact_digest")
    require(
        isinstance(trusted_workflow, str)
        and re.fullmatch(
            r"Celoria-Industries/\.github/\.github/workflows/staging-terraform-plan\.yml@[0-9a-f]{40}",
            trusted_workflow,
        ) is not None,
        "STAGING_TRUSTED_PLAN_WORKFLOW must pin staging-terraform-plan.yml to a full commit SHA",
    )
    require(source["producer_workflow_ref"] == trusted_workflow,
            "Terraform producer does not match the admin-configured immutable workflow")
    require(source["signing_algorithm"] == "RSASSA_PSS_SHA_256",
            "Terraform evidence must use RSASSA_PSS_SHA_256")
    require(
        isinstance(source["signing_key_arn"], str)
        and re.fullmatch(
            r"arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[0-9a-f-]{36}",
            source["signing_key_arn"],
        ) is not None,
        "Terraform signing_key_arn must be an immutable KMS key ARN",
    )
    require(
        isinstance(source["target_account_id"], str)
        and re.fullmatch(r"[0-9]{12}", source["target_account_id"]) is not None,
        "Terraform target_account_id must be a 12-digit AWS account ID",
    )
    require(
        isinstance(source["target_region"], str)
        and re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]", source["target_region"]) is not None,
        "Terraform target_region is invalid",
    )
    require(
        isinstance(source["terraform_root"], str)
        and re.fullmatch(r"stacks/[a-z0-9][a-z0-9-]*/staging/[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*", source["terraform_root"]) is not None,
        "Terraform root must be a staging stack path",
    )
    for key, value in expected.items():
        require(source.get(key) == value, f"Terraform promotion does not match configured {key}")
    return source


def list_run_artifacts(api, repository, run_id):
    artifacts = []
    for page in range(1, 101):
        response = api.get(
            f"/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100&page={page}"
        )
        page_artifacts = response.get("artifacts", [])
        require(isinstance(page_artifacts, list), "Terraform artifact list response was invalid")
        artifacts.extend(page_artifacts)
        total_count = response.get("total_count")
        require(type(total_count) is int and total_count >= len(artifacts),
                "Terraform artifact list omitted a valid total_count")
        if len(artifacts) >= total_count:
            return artifacts
    fail("Terraform producer run has too many artifacts to verify safely")


def validate_terraform_artifact(api, repository, artifact_id, name, digest, run_id):
    named_artifacts = [
        item for item in list_run_artifacts(api, repository, run_id)
        if item.get("name") == name
    ]
    require(len(named_artifacts) == 1,
            f"Terraform producer run must contain exactly one {name} artifact")
    require(named_artifacts[0].get("id") == artifact_id,
            f"Terraform {name} artifact ID is stale or substituted")
    artifact = api.get(f"/repos/{repository}/actions/artifacts/{artifact_id}")
    require(artifact.get("id") == artifact_id, f"Terraform artifact ID {artifact_id} changed")
    require(artifact.get("name") == name, f"Terraform artifact {artifact_id} has an unexpected name")
    require(artifact.get("digest") == digest, f"Terraform artifact {artifact_id} digest does not match")
    require(artifact.get("expired") is False, f"Terraform artifact {artifact_id} has expired")
    require((artifact.get("workflow_run") or {}).get("id") == run_id,
            f"Terraform artifact {artifact_id} belongs to a different producer run")


def validate_source_terraform_run(api, repository, source, trusted_workflow):
    source_pr, source_head = validate_reviewed_pr(
        api, repository, source["pull_request_number"]
    )
    require(source_head == source["pull_request_head_sha"],
            "Terraform source PR head changed after plan creation")
    merge_sha = validate_full_sha(source_pr.get("merge_commit_sha"), "Terraform source PR merge SHA")
    require(merge_sha == source["pull_request_merge_sha"],
            "Terraform source PR merge commit does not match the plan evidence")
    require(source["commit"] == merge_sha, "Terraform source commit is not the reviewed PR merge commit")

    run = api.get(
        f'/repos/{repository}/actions/runs/{source["workflow_run_id"]}'
        f'/attempts/{source["workflow_run_attempt"]}'
    )
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "Terraform plan workflow run did not complete successfully")
    require(run.get("event") == "workflow_dispatch" and run.get("head_branch") == "main",
            "Terraform plan workflow must be dispatched from main")
    require(run.get("head_sha") == source["workflow_run_head_sha"],
            "Terraform plan workflow commit does not match the manifest")
    require(run.get("run_attempt") == source["workflow_run_attempt"],
            "Terraform plan workflow run attempt does not match")
    require(run.get("path", "").split("@", 1)[0] == PLAN_WORKFLOW_PATH,
            "Terraform plan run used an unexpected caller workflow")
    relation = api.get(
        f"/repos/{repository}/compare/{merge_sha}...{source['workflow_run_head_sha']}"
    )
    require(relation.get("status") in ("identical", "ahead"),
            "Terraform source merge commit is not an ancestor of the producer run")
    wrapper = repository_file(api, repository, PLAN_WORKFLOW_PATH, source["workflow_run_head_sha"])
    uses = re.findall(r"(?m)^\s+uses:\s*([^\s#]+)\s*(?:#.*)?$", wrapper)
    has_steps_or_commands = re.search(r"(?m)^[ \t]*(?:steps|run)\s*:", wrapper) is not None
    require(uses == [trusted_workflow] and not has_steps_or_commands,
            "Terraform source workflow must only call the pinned reusable plan workflow")

    validate_terraform_artifact(
        api,
        repository,
        source["plan_artifact_id"],
        source["plan_artifact_name"],
        source["plan_artifact_digest"],
        source["workflow_run_id"],
    )
    validate_terraform_artifact(
        api,
        repository,
        source["evidence_artifact_id"],
        source["evidence_artifact_name"],
        source["evidence_artifact_digest"],
        source["workflow_run_id"],
    )
    return source_pr


def verify_local_terraform_artifacts(source, expected):
    plan_path = os.environ.get("PLAN_ARTIFACT_PATH", "")
    record_path = os.environ.get("EVIDENCE_RECORD_PATH", "")
    signature_path = os.environ.get("EVIDENCE_SIGNATURE_PATH", "")
    paths = (plan_path, record_path, signature_path)
    require(not any(paths) or all(paths), "Terraform plan artifact verification paths are incomplete")
    if not any(paths):
        return
    try:
        with open(record_path, "rb") as record_file:
            record = strict_record(record_file.read())
        with open(signature_path, "r", encoding="ascii") as signature_file:
            signature = signature_file.read().strip()
    except (OSError, UnicodeDecodeError) as error:
        raise GateError(f"Terraform evidence artifact could not be read: {error}") from error
    record_expected = dict(expected)
    record_expected.update({
        "source_repository": source["repository"],
        "source_commit": source["commit"],
        "source_pull_request_number": source["pull_request_number"],
        "source_pull_request_head_sha": source["pull_request_head_sha"],
        "source_pull_request_merge_sha": source["pull_request_merge_sha"],
        "producer_run_id": source["workflow_run_id"],
        "producer_run_attempt": source["workflow_run_attempt"],
        "producer_run_head_sha": source["workflow_run_head_sha"],
        "producer_workflow_ref": source["producer_workflow_ref"],
        "plan_artifact_id": source["plan_artifact_id"],
        "plan_artifact_name": source["plan_artifact_name"],
        "plan_artifact_digest": source["plan_artifact_digest"],
        "plan_sha256": source["plan_sha256"],
        "target_account_id": source["target_account_id"],
        "target_region": source["target_region"],
        "terraform_root": source["terraform_root"],
        "signing_key_arn": source["signing_key_arn"],
        "signing_algorithm": source["signing_algorithm"],
    })
    try:
        verify_plan_evidence(
            record,
            signature,
            source["signing_algorithm"],
            os.environ.get("STAGING_PLAN_SIGNING_PUBLIC_KEY_DER_B64", ""),
            record_expected,
        )
        verify_plan_bytes(record, plan_path)
    except EvidenceError as error:
        raise GateError(str(error)) from error


def write_outputs(values):
    output_path = os.environ.get("GITHUB_OUTPUT")
    require(bool(output_path), "GITHUB_OUTPUT is unavailable")
    with open(output_path, "a", encoding="utf-8") as output:
        for name, value in values.items():
            require("\n" not in str(value) and "\r" not in str(value),
                    f"Output {name} contains a newline")
            output.write(f"{name}={value}\n")


def validate_plan_producer_trigger(event_name, ref):
    require(
        event_name == "workflow_dispatch" and ref == "refs/heads/main",
        "Terraform plan producer may run only by workflow_dispatch from main",
    )


def main():
    mode = os.environ.get("VERIFICATION_MODE", "promotion")
    if mode == "source-pr":
        validate_plan_producer_trigger(
            os.environ.get("GITHUB_EVENT_NAME", ""),
            os.environ.get("GITHUB_REF", ""),
        )
        repository = validate_repo(os.environ.get("GITHUB_REPOSITORY"), "GITHUB_REPOSITORY")
        require(repository == PLAN_REPOSITORY,
                f"Terraform plan producer is restricted to {PLAN_REPOSITORY}")
        if approval_mode() == "solo":
            require(os.environ.get("STAGING_PLAN_TARGET_ACCOUNT_ID") == os.environ.get("STAGING_SOLO_TARGET_ACCOUNT_ID")
                    and os.environ.get("STAGING_PLAN_TARGET_REGION") == os.environ.get("STAGING_SOLO_TARGET_REGION"),
                    "Solo source authorization does not match the Terraform staging target")
        pr_number = parse_positive_int(os.environ.get("SOURCE_PR_NUMBER"), "source-pr-number")
        api = GitHubApi()
        source_pr, head_sha = validate_reviewed_pr(api, repository, pr_number)
        merge_sha = validate_full_sha(source_pr.get("merge_commit_sha"), f"PR #{pr_number} merge SHA")
        write_outputs({
            "source_pr_number": pr_number,
            "source_pull_request_head_sha": head_sha,
            "source_pull_request_merge_sha": merge_sha,
            "source_commit": merge_sha,
        })
        print(f"Verified reviewed source PR #{pr_number} at merge commit {merge_sha}")
        return
    require(mode == "promotion", "Unsupported promotion gate verification mode")
    validate_promotion_trigger(
        os.environ.get("GITHUB_EVENT_NAME", ""),
        os.environ.get("GITHUB_REF", ""),
    )
    repository = validate_repo(os.environ.get("GITHUB_REPOSITORY"), "GITHUB_REPOSITORY")
    pr_number = parse_positive_int(os.environ.get("PROMOTION_PR_NUMBER"), "promotion-pr-number")
    expected_manifest_sha = os.environ.get("EXPECTED_MANIFEST_SHA", "")
    api = GitHubApi()
    promotion_pr, head_sha = validate_reviewed_pr(api, repository, pr_number, promotion=True)
    path = urllib.parse.quote(MANIFEST_PATH, safe="/")
    response = api.get(f"/repos/{repository}/contents/{path}?ref={head_sha}")
    require(response.get("encoding") == "base64" and isinstance(response.get("content"), str),
            "Promotion manifest could not be read as base64")
    try:
        manifest_bytes = base64.b64decode(response["content"].replace("\n", ""), validate=True)
    except ValueError as error:
        raise GateError("Promotion manifest has invalid base64 content") from error
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    require(not expected_manifest_sha or manifest_sha == expected_manifest_sha,
            "Promotion manifest changed after the initial verification")
    manifest = strict_json(manifest_bytes, MANIFEST_PATH)
    kind = manifest.get("kind") if isinstance(manifest, dict) else None
    validate_supported_kind(kind)
    solo_created = ""
    target_resource = ""
    if kind == "image":
        trusted_workflow = os.environ.get("TRUSTED_IMAGE_PRODUCER_WORKFLOW", "")
        source, _ = validate_image_manifest(manifest, repository, trusted_workflow)
        source_pr = validate_source_image_run(api, repository, source, trusted_workflow)
        if approval_mode() == "solo":
            registry = os.environ.get("STAGING_ECR_REGISTRY", "")
            account = os.environ.get("STAGING_SOLO_TARGET_ACCOUNT_ID", "")
            region = os.environ.get("STAGING_SOLO_TARGET_REGION", "")
            require(registry == f"{account}.dkr.ecr.{region}.amazonaws.com",
                    "Solo image registry does not match the authorized staging account and region")
            target_resource = f"{registry}/{source['image_repository']}"
            solo_created = verify_solo_pr(api, repository, pr_number, head_sha, "promotion", {
                "manifest_sha256": f"sha256:{manifest_sha}",
                "target_resource": target_resource,
            })
        outputs = {
            "promotion_kind": kind,
            "source_commit": source["commit"],
            "source_repository": source["repository"],
            "image_repository": source["image_repository"],
            "image_digest": source["image_digest"],
            "manifest_sha256": manifest_sha,
        }
    else:
        trusted_workflow = os.environ.get("TRUSTED_PLAN_PRODUCER_WORKFLOW", "")
        expected = {
            "target_account_id": os.environ.get("STAGING_PLAN_TARGET_ACCOUNT_ID", ""),
            "target_region": os.environ.get("STAGING_PLAN_TARGET_REGION", ""),
            "terraform_root": os.environ.get("STAGING_PLAN_ROOT", ""),
            "signing_key_arn": os.environ.get("STAGING_PLAN_SIGNING_KEY_ARN", ""),
        }
        require(all(expected.values()), "Staging Terraform target, root, and signing key must be configured")
        source = validate_terraform_manifest(manifest, repository, trusted_workflow, expected)
        if approval_mode() == "solo":
            require(source["target_account_id"] == os.environ.get("STAGING_SOLO_TARGET_ACCOUNT_ID")
                    and source["target_region"] == os.environ.get("STAGING_SOLO_TARGET_REGION"),
                    "Solo authorization target does not match the Terraform target")
            target_resource = source["terraform_root"]
            solo_created = verify_solo_pr(api, repository, pr_number, head_sha, "promotion", {
                "manifest_sha256": f"sha256:{manifest_sha}",
                "target_resource": target_resource,
            })
        source_pr = validate_source_terraform_run(api, repository, source, trusted_workflow)
        verify_local_terraform_artifacts(source, expected)
        outputs = {
            "promotion_kind": kind,
            "source_commit": source["commit"],
            "source_repository": source["repository"],
            "image_repository": "",
            "image_digest": "",
            "manifest_sha256": manifest_sha,
            "producer_run_id": source["workflow_run_id"],
            "plan_artifact_id": source["plan_artifact_id"],
            "evidence_artifact_id": source["evidence_artifact_id"],
        }

    outputs["approval_mode"] = approval_mode()
    outputs["promotion_head_sha"] = head_sha
    outputs["source_pr_number"] = source_pr["number"]
    outputs["source_pull_request_head_sha"] = source_pr["head"]["sha"]
    outputs["solo_authorization_created_at"] = solo_created
    outputs["solo_target_resource"] = target_resource
    outputs["solo_source_authorization_created_at"] = (
        verify_solo_pr(api, repository, source_pr["number"], source_pr["head"]["sha"], "source")
        if approval_mode() == "solo" else ""
    )
    if approval_mode() == "solo":
        require(outputs["solo_source_authorization_created_at"] <= solo_created,
                "Promotion authorization must not predate source authorization")
    write_outputs(outputs)
    print(f"Verified {kind} staging promotion in PR #{pr_number} at {head_sha}")


if __name__ == "__main__":
    try:
        main()
    except GateError as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
