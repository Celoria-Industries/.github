import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


MANIFEST_PATH = ".github/staging-promotion.json"
IMAGE_WORKFLOW_PATH = ".github/workflows/container-image.yml"


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
    if kind == "terraform":
        fail(
            "Terraform staging promotion is disabled: GitHub Team plans cannot attest private-repository "
            "artifacts; a different trusted plan-provenance mechanism must be implemented and verified"
        )
    require(kind == "image", "Promotion manifest kind must be image")


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
    require(source["producer_workflow_ref"] == trusted_workflow,
            "Image producer does not match the admin-configured immutable workflow")
    wrapper = repository_file(api, repository, IMAGE_WORKFLOW_PATH, source["commit"])
    uses = re.findall(r"(?m)^\s+uses:\s*([^\s#]+)\s*(?:#.*)?$", wrapper)
    has_run_step = re.search(r"(?m)^[ \t]*(?:-\s*)?run\s*:", wrapper) is not None
    require(uses == [trusted_workflow] and not has_run_step,
            "Source workflow must only call the pinned reusable image workflow, with no run steps")


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
    validate_supported_kind(kind)
    if kind == "image":
        trusted_workflow = os.environ.get("TRUSTED_IMAGE_PRODUCER_WORKFLOW", "")
        source, _ = validate_image_manifest(manifest, repository, trusted_workflow)
        validate_source_image_run(api, repository, source, trusted_workflow)

    write_outputs({
        "promotion_kind": kind,
        "source_commit": source["commit"],
        "source_repository": source["repository"],
        "image_repository": source.get("image_repository", ""),
        "image_digest": source.get("image_digest", ""),
        "manifest_sha256": manifest_sha,
    })
    print(f"Verified {kind} staging promotion in PR #{pr_number} at {head_sha}")


if __name__ == "__main__":
    try:
        main()
    except GateError as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
