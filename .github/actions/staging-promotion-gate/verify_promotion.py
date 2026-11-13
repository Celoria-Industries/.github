import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile


MANIFEST_PATH = ".github/staging-promotion.json"
IMAGE_WORKFLOW_PATH = ".github/workflows/container-image.yml"
PLAN_ARTIFACT_NAME = "staging-tfplan"
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024


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

    def download(self, path, destination):
        url = urllib.parse.urljoin(self.base + "/", path.lstrip("/"))
        require(urllib.parse.urlsplit(url)[:2] == self.base_origin, "GitHub artifact URL escaped its configured host")
        try:
            response = self._request_without_redirect(url)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                fail(f"GitHub artifact download failed ({error.code})")
            location = error.headers.get("Location")
            require(location is not None, "Artifact download redirect has no location")
            parsed = urllib.parse.urlsplit(location)
            require(parsed.scheme == "https" and parsed.hostname, "Artifact download redirect is not HTTPS")
            response = urllib.request.urlopen(
                urllib.request.Request(location, headers={"User-Agent": "Celoria-Staging-Promotion-Gate"}),
                timeout=60,
            )
        total = 0
        with response, open(destination, "wb") as target:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                require(total <= MAX_ARTIFACT_BYTES, "Saved-plan artifact exceeds the 1 GiB safety limit")
                target.write(chunk)

    def _request_without_redirect(self, url):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, response, code, message, headers, new_url):
                return None

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Celoria-Staging-Promotion-Gate",
            "Authorization": f"Bearer {self.token}",
        }
        opener = urllib.request.build_opener(NoRedirect)
        return opener.open(urllib.request.Request(url, headers=headers), timeout=30)


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
    require(approved, f"PR #{number} lacks a current human approval on its exact head")

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


def validate_image_manifest(manifest, repository):
    require_object(manifest, ("schema_version", "target", "kind", "source", "destination"), "manifest")
    require(manifest["schema_version"] == 1, "Unsupported promotion manifest schema_version")
    require(manifest["target"] == "staging" and manifest["kind"] == "image",
            "Image promotion manifest must target staging")
    source = require_object(
        manifest["source"],
        ("repository", "commit", "workflow_run_id", "workflow_run_attempt", "image_repository", "image_digest"),
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


def validate_plan_manifest(manifest, repository, trusted_workflow, terraform_directory):
    require_object(manifest, ("schema_version", "target", "kind", "source", "destination"), "manifest")
    require(manifest["schema_version"] == 1, "Unsupported promotion manifest schema_version")
    require(manifest["target"] == "staging" and manifest["kind"] == "terraform",
            "Terraform promotion manifest must target staging")
    source = require_object(
        manifest["source"],
        (
            "repository",
            "commit",
            "pull_request_number",
            "workflow_run_id",
            "workflow_run_attempt",
            "artifact_id",
            "artifact_name",
            "artifact_digest",
            "plan_sha256",
            "producer_workflow",
        ),
        "source",
    )
    require(validate_repo(source["repository"], "source.repository") == repository,
            "Terraform source repository must match the caller repository")
    validate_full_sha(source["commit"], "source.commit")
    source["pull_request_number"] = parse_positive_int(
        source["pull_request_number"], "source.pull_request_number"
    )
    source["workflow_run_id"] = parse_positive_int(source["workflow_run_id"], "source.workflow_run_id")
    source["workflow_run_attempt"] = parse_positive_int(
        source["workflow_run_attempt"], "source.workflow_run_attempt"
    )
    source["artifact_id"] = parse_positive_int(source["artifact_id"], "source.artifact_id")
    require(source["artifact_name"] == PLAN_ARTIFACT_NAME,
            f"Terraform artifact_name must be {PLAN_ARTIFACT_NAME}")
    validate_digest(source["artifact_digest"], "source.artifact_digest")
    validate_digest(source["plan_sha256"], "source.plan_sha256")
    producer = require_object(source["producer_workflow"], ("path", "sha"), "source.producer_workflow")
    require(isinstance(producer["path"], str) and re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/[A-Za-z0-9_.-]+\.yml",
        producer["path"],
    ), "source.producer_workflow.path must identify a GitHub Actions workflow")
    validate_full_sha(producer["sha"], "source.producer_workflow.sha")
    require(f'{producer["path"]}@{producer["sha"]}' == trusted_workflow,
            "Terraform producer does not match the admin-configured immutable workflow")
    destination = require_object(manifest["destination"], ("working_directory",), "destination")
    require(destination["working_directory"] == terraform_directory and bool(terraform_directory),
            "Terraform destination must match STAGING_TERRAFORM_WORKING_DIRECTORY")
    path = PurePosixPath(destination["working_directory"])
    require(not path.is_absolute() and ".." not in path.parts,
            "Terraform destination working_directory must stay inside the repository")
    return source, destination


def validate_source_image_run(api, repository, source):
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
    require(run.get("path") == IMAGE_WORKFLOW_PATH, "Source image run used an unexpected workflow")


def validate_plan_run_and_artifact(api, repository, source, plan_path):
    source_pr, _ = validate_reviewed_pr(
        api, repository, source["pull_request_number"], required_head=source["commit"]
    )
    run = api.get(
        f'/repos/{repository}/actions/runs/{source["workflow_run_id"]}'
        f'/attempts/{source["workflow_run_attempt"]}'
    )
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "Terraform plan workflow run did not complete successfully")
    require(run.get("event") == "pull_request" and run.get("head_sha") == source["commit"],
            "Terraform plan run must be from the exact reviewed source PR commit")
    require(run.get("run_attempt") == source["workflow_run_attempt"], "Terraform plan run attempt does not match")
    require(any(
        pull.get("number") == source["pull_request_number"]
        and pull.get("head", {}).get("sha") == source["commit"]
        and pull.get("base", {}).get("ref") == "main"
        for pull in run.get("pull_requests", [])
    ), "Terraform plan run is not associated with the reviewed source PR")

    artifact = api.get(f'/repos/{repository}/actions/artifacts/{source["artifact_id"]}')
    require(not artifact.get("expired"), "Terraform plan artifact has expired")
    require(artifact.get("name") == PLAN_ARTIFACT_NAME, "Terraform plan artifact name does not match")
    require(artifact.get("digest") == source["artifact_digest"], "Terraform artifact digest does not match")
    run_info = artifact.get("workflow_run") or {}
    require(run_info.get("id") == source["workflow_run_id"]
            and run_info.get("head_sha") == source["commit"],
            "Terraform artifact is not from the specified source run and commit")
    try:
        artifact_created = datetime.fromisoformat(artifact["created_at"].replace("Z", "+00:00"))
        attempt_started = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
        attempt_updated = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as error:
        raise GateError("GitHub API did not provide timestamps to bind the artifact to its run attempt") from error
    require(attempt_started <= artifact_created <= attempt_updated,
            "Terraform artifact was not created during the specified workflow run attempt")

    with tempfile.TemporaryDirectory(prefix="celoria-plan-") as directory:
        archive = os.path.join(directory, "artifact.zip")
        api.download(f'/repos/{repository}/actions/artifacts/{source["artifact_id"]}/zip', archive)
        with open(archive, "rb") as artifact_file:
            digest = hashlib.sha256()
            while True:
                chunk = artifact_file.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            archive_digest = "sha256:" + digest.hexdigest()
        require(archive_digest == source["artifact_digest"], "Downloaded Terraform artifact digest does not match")
        with zipfile.ZipFile(archive) as zipped:
            members = [member for member in zipped.infolist() if not member.is_dir()]
            require(len(members) == 1 and members[0].filename == "tfplan",
                    "Terraform artifact must contain exactly one file named tfplan")
            plan = zipped.read(members[0])
        plan_digest = "sha256:" + hashlib.sha256(plan).hexdigest()
        require(plan_digest == source["plan_sha256"], "Saved Terraform plan SHA-256 does not match")
        os.makedirs(os.path.dirname(plan_path), exist_ok=True)
        with open(plan_path, "wb") as plan_file:
            plan_file.write(plan)

    producer = source["producer_workflow"]
    command = [
        "gh", "attestation", "verify", plan_path,
        "--repo", repository,
        "--signer-workflow", producer["path"],
        "--signer-digest", producer["sha"],
        "--source-digest", source["commit"],
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise GateError("GitHub CLI with attestation verification is required") from error
    require(result.returncode == 0,
            f"Saved Terraform plan attestation verification failed: {result.stderr.strip()}")
    return source_pr


def write_outputs(values):
    output_path = os.environ.get("GITHUB_OUTPUT")
    require(bool(output_path), "GITHUB_OUTPUT is unavailable")
    with open(output_path, "a", encoding="utf-8") as output:
        for name, value in values.items():
            require("\n" not in str(value) and "\r" not in str(value),
                    f"Output {name} contains a newline")
            output.write(f"{name}={value}\n")


def main():
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
    source = None
    destination = None
    plan_path = ""
    if kind == "image":
        source, destination = validate_image_manifest(manifest, repository)
        validate_source_image_run(api, repository, source)
    elif kind == "terraform":
        source, destination = validate_plan_manifest(
            manifest,
            repository,
            os.environ.get("TRUSTED_TERRAFORM_PRODUCER_WORKFLOW", ""),
            os.environ.get("TERRAFORM_WORKING_DIRECTORY", ""),
        )
        plan_path = os.path.join(
            tempfile.gettempdir(), f"celoria-staging-tfplan-{pr_number}-{source['artifact_id']}"
        )
        validate_plan_run_and_artifact(api, repository, source, plan_path)
    else:
        fail("Promotion manifest kind must be image or terraform")

    write_outputs({
        "promotion_kind": kind,
        "source_commit": source["commit"],
        "source_repository": source["repository"],
        "image_repository": source.get("image_repository", ""),
        "image_digest": source.get("image_digest", ""),
        "plan_sha256": source.get("plan_sha256", ""),
        "plan_path": plan_path,
        "manifest_sha256": manifest_sha,
    })
    print(f"Verified {kind} staging promotion in PR #{pr_number} at {head_sha}")


if __name__ == "__main__":
    try:
        main()
    except GateError as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
