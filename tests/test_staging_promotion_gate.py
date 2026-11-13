import unittest
import subprocess

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/actions/staging-promotion-gate"))
STAGING_WORKFLOW = (ROOT / ".github/workflows/staging-promotion-gate.yml").read_text()

from verify_promotion import (  # noqa: E402
    GateError,
    validate_image_manifest,
    validate_promotion_trigger,
    validate_reviewed_pr,
    validate_source_image_run,
    validate_terraform_artifact,
    validate_terraform_manifest,
    validate_supported_kind,
)


HEAD_SHA = "a" * 40
SOURCE_SHA = "b" * 40
PRODUCER_SHA = "c" * 40
IMAGE_WORKFLOW_REF = (
    "Celoria-Industries/.github/.github/workflows/container-image.yml@" + PRODUCER_SHA
)
PLAN_WORKFLOW_REF = (
    "Celoria-Industries/.github/.github/workflows/staging-terraform-plan.yml@" + PRODUCER_SHA
)


def pr(review_head=HEAD_SHA):
    return {
        "state": "closed",
        "merged_at": "2026-11-12T10:00:00Z",
        "changed_files": 1,
        "base": {"ref": "main", "repo": {"full_name": "celoria/service"}},
        "head": {"sha": HEAD_SHA, "repo": {"full_name": "celoria/service"}},
        "user": {"login": "contributor", "type": "User"},
        "merge_commit_sha": "d" * 40,
        "review_head": review_head,
    }


def approval(login="reviewer", review_head=HEAD_SHA, state="APPROVED", user_type="User"):
    return {
        "state": state,
        "commit_id": review_head,
        "submitted_at": "2026-11-12T09:00:00Z",
        "user": {"login": login, "type": user_type},
    }


class FakeApi:
    def __init__(self, pr_data=None, reviews=None, checks=None, files=None):
        self.pr_data = pr_data or pr()
        self.reviews = reviews if reviews is not None else [approval()]
        self.checks = checks if checks is not None else {
            "check_runs": [{"status": "completed", "conclusion": "success"}]
        }
        self.files = files if files is not None else [{"filename": ".github/staging-promotion.json"}]

    def get(self, path):
        if path == "/repos/celoria/service/pulls/7":
            return self.pr_data
        if "/commits/" in path and "/check-runs?filter=latest&per_page=100&page=" in path:
            return self.checks
        if path.endswith("/pulls/7/files?per_page=2"):
            return self.files
        raise AssertionError(f"Unexpected API request: {path}")

    def list(self, path):
        if path == "/repos/celoria/service/pulls/7/reviews":
            return self.reviews
        raise AssertionError(f"Unexpected API list request: {path}")


class StagingPromotionGateTest(unittest.TestCase):
    def test_promotion_fails_before_credential_job(self):
        for job, guard in (
            ("verify", "Stop disabled staging promotion"),
            ("promote", "Stop disabled staging writer"),
        ):
            with self.subTest(job=job):
                job_body = STAGING_WORKFLOW.split(f"\n  {job}:\n", 1)[1].split("\n  promote:\n", 1)[0]
                first_step = job_body.split("    steps:\n", 1)[1].split("\n      - name:", 1)[0]
                self.assertIn(f"- name: {guard}", first_step)
                script = first_step.split("        run: |\n", 1)[1]
                result = subprocess.run(
                    ["bash", "-c", "\n".join(line[10:] for line in script.splitlines())],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("disabled pending security and integrity validation", result.stdout)
                self.assertNotIn("continue-on-error", first_step)
        promote = STAGING_WORKFLOW.split("\n  promote:\n", 1)[1]
        self.assertIn("    needs: [verify, consume]\n", promote)
        self.assertNotIn("    if:", promote.split("    steps:\n", 1)[0])

    def test_promotion_must_be_invoked_from_main(self):
        validate_promotion_trigger("workflow_dispatch", "refs/heads/main")
        for event, ref in (
            ("pull_request", "refs/pull/42/merge"),
            ("workflow_dispatch", "refs/heads/feature"),
        ):
            with self.subTest(event=event, ref=ref):
                with self.assertRaisesRegex(GateError, "only from a push or manual dispatch on main"):
                    validate_promotion_trigger(event, ref)

    def test_requires_exact_head_human_approval_and_successful_checks(self):
        validated_pr, head_sha = validate_reviewed_pr(FakeApi(), "celoria/service", 7, promotion=True)
        self.assertEqual(validated_pr["merged_at"], "2026-11-12T10:00:00Z")
        self.assertEqual(head_sha, HEAD_SHA)

    def test_rejects_stale_approval(self):
        with self.assertRaisesRegex(GateError, "exact head"):
            validate_reviewed_pr(
                FakeApi(reviews=[approval(review_head="e" * 40)]),
                "celoria/service",
                7,
                promotion=True,
            )

    def test_rejects_approval_followed_by_change_request(self):
        with self.assertRaisesRegex(GateError, "unresolved change requests"):
            validate_reviewed_pr(
                FakeApi(reviews=[
                    approval(login="reviewer"),
                    {
                        "state": "CHANGES_REQUESTED",
                        "commit_id": HEAD_SHA,
                        "submitted_at": "2026-11-12T09:30:00Z",
                        "user": {"login": "second-reviewer", "type": "User"},
                    },
                ]),
                "celoria/service",
                7,
                promotion=True,
            )

    def test_rejects_author_and_bot_approvals(self):
        for review in (approval(login="contributor"), approval(login="automation[bot]", user_type="Bot")):
            with self.subTest(review=review):
                with self.assertRaisesRegex(GateError, "exact head"):
                    validate_reviewed_pr(
                        FakeApi(reviews=[review]),
                        "celoria/service",
                        7,
                        promotion=True,
                    )

    def test_rejects_failed_checks_and_extra_pr_files(self):
        with self.assertRaisesRegex(GateError, "failed"):
            validate_reviewed_pr(
                FakeApi(checks={"check_runs": [{"status": "completed", "conclusion": "failure"}]}),
                "celoria/service",
                7,
                promotion=True,
            )
        with self.assertRaisesRegex(GateError, "only .github/staging-promotion.json"):
            validate_reviewed_pr(
                FakeApi(files=[
                    {"filename": ".github/staging-promotion.json"},
                    {"filename": "infra/main.tf"},
                ]),
                "celoria/service",
                7,
                promotion=True,
            )

    def test_rejects_unmerged_or_wrong_base_pr(self):
        data = pr()
        data["merged_at"] = None
        with self.assertRaisesRegex(GateError, "not merged"):
            validate_reviewed_pr(FakeApi(pr_data=data), "celoria/service", 7, promotion=True)
        data = pr()
        data["base"]["ref"] = "develop"
        with self.assertRaisesRegex(GateError, "must target main"):
            validate_reviewed_pr(FakeApi(pr_data=data), "celoria/service", 7, promotion=True)

    def test_image_manifest_binds_digest_to_staging_repository(self):
        manifest = {
            "schema_version": 1,
            "target": "staging",
            "kind": "image",
            "source": {
                "repository": "celoria/service",
                "commit": SOURCE_SHA,
                "workflow_run_id": 123,
                "workflow_run_attempt": 1,
                "image_repository": "service",
                "image_digest": "sha256:" + "1" * 64,
                "producer_workflow_ref": IMAGE_WORKFLOW_REF,
            },
            "destination": {"image_repository": "service"},
        }
        source, destination = validate_image_manifest(manifest, "celoria/service", IMAGE_WORKFLOW_REF)
        self.assertEqual(source["image_digest"], manifest["source"]["image_digest"])
        self.assertEqual(destination["image_repository"], "service")

    def test_rejects_wrong_image_target_and_destination(self):
        manifest = {
            "schema_version": 1,
            "target": "production",
            "kind": "image",
            "source": {
                "repository": "celoria/service",
                "commit": SOURCE_SHA,
                "workflow_run_id": 123,
                "workflow_run_attempt": 1,
                "image_repository": "service",
                "image_digest": "sha256:" + "1" * 64,
                "producer_workflow_ref": IMAGE_WORKFLOW_REF,
            },
            "destination": {"image_repository": "service"},
        }
        with self.assertRaisesRegex(GateError, "target staging"):
            validate_image_manifest(manifest, "celoria/service", IMAGE_WORKFLOW_REF)
        manifest["target"] = "staging"
        manifest["destination"]["image_repository"] = "other"
        with self.assertRaisesRegex(GateError, "must match"):
            validate_image_manifest(manifest, "celoria/service", IMAGE_WORKFLOW_REF)
        manifest["destination"]["image_repository"] = "service"
        manifest["source"]["producer_workflow_ref"] = IMAGE_WORKFLOW_REF.replace(PRODUCER_SHA, "main")
        with self.assertRaisesRegex(GateError, "admin-configured immutable workflow"):
            validate_image_manifest(manifest, "celoria/service", IMAGE_WORKFLOW_REF)

    def test_verifies_image_run_and_rejects_caller_steps(self):
        run = {
            "status": "completed",
            "conclusion": "success",
            "event": "push",
            "head_branch": "main",
            "head_sha": SOURCE_SHA,
            "run_attempt": 1,
            "path": ".github/workflows/image.yml@refs/heads/main",
        }
        source_pr = pr()
        source_pr.update({"number": 17, "merge_commit_sha": SOURCE_SHA})

        class SourceApi:
            def __init__(self, wrapper, pull_requests=None, reviews=None):
                self.wrapper = wrapper
                self.source_prs = pull_requests if pull_requests is not None else [source_pr]
                self.reviews = reviews if reviews is not None else [approval()]

            def get(self, path):
                if path == "/repos/celoria/service/actions/runs/123/attempts/1":
                    return run
                if path == "/repos/celoria/service/pulls/17":
                    return source_pr
                if "/check-runs?filter=latest&per_page=100&page=" in path:
                    return {
                        "check_runs": [{"status": "completed", "conclusion": "success"}],
                        "total_count": 1,
                    }
                if path == f"/repos/celoria/service/contents/.github/workflows/image.yml?ref={SOURCE_SHA}":
                    import base64
                    return {
                        "encoding": "base64",
                        "content": base64.b64encode(self.wrapper.encode()).decode(),
                    }
                raise AssertionError(f"Unexpected API request: {path}")

            def list(self, path):
                if path == f"/repos/celoria/service/commits/{SOURCE_SHA}/pulls":
                    return self.source_prs
                if path == "/repos/celoria/service/pulls/17/reviews":
                    return self.reviews
                raise AssertionError(f"Unexpected API list request: {path}")

        source = {
            "commit": SOURCE_SHA,
            "workflow_run_id": 123,
            "workflow_run_attempt": 1,
            "producer_workflow_ref": IMAGE_WORKFLOW_REF,
        }
        wrapper = f"name: Build\njobs:\n  image:\n    uses: {IMAGE_WORKFLOW_REF}\n"
        validate_source_image_run(SourceApi(wrapper), "celoria/service", source, IMAGE_WORKFLOW_REF)
        with self.assertRaisesRegex(GateError, "only call the pinned reusable image workflow"):
            validate_source_image_run(
                SourceApi(wrapper + "    steps:\n      - run: echo unsafe\n"),
                "celoria/service",
                source,
                IMAGE_WORKFLOW_REF,
            )
        with self.assertRaisesRegex(GateError, "exactly one merged PR"):
            validate_source_image_run(
                SourceApi(wrapper, pull_requests=[]),
                "celoria/service",
                source,
                IMAGE_WORKFLOW_REF,
            )
        with self.assertRaisesRegex(GateError, "current human approval"):
            validate_source_image_run(
                SourceApi(wrapper, reviews=[]),
                "celoria/service",
                source,
                IMAGE_WORKFLOW_REF,
            )

    def test_terraform_manifest_binds_separate_artifacts_and_target(self):
        manifest = {
            "schema_version": 1,
            "target": "staging",
            "kind": "terraform",
            "source": {
                "repository": "Celoria-Industries/infrastructure-as-code",
                "commit": "d" * 40,
                "pull_request_number": 91,
                "pull_request_head_sha": "a" * 40,
                "pull_request_merge_sha": "d" * 40,
                "workflow_run_id": 101,
                "workflow_run_attempt": 1,
                "workflow_run_head_sha": "e" * 40,
                "producer_workflow_ref": PLAN_WORKFLOW_REF,
                "plan_artifact_id": 102,
                "plan_artifact_name": "staging-terraform-plan",
                "plan_artifact_digest": "sha256:" + "1" * 64,
                "plan_sha256": "sha256:" + "2" * 64,
                "evidence_artifact_id": 103,
                "evidence_artifact_name": "staging-terraform-evidence",
                "evidence_artifact_digest": "sha256:" + "3" * 64,
                "target_account_id": "123456789012",
                "target_region": "us-east-2",
                "terraform_root": "stacks/celoria-industries/staging/foundation",
                "signing_key_arn": "arn:aws:kms:us-east-2:999999999999:key/12345678-1234-1234-1234-123456789012",
                "signing_algorithm": "RSASSA_PSS_SHA_256",
            },
        }
        source = validate_terraform_manifest(
            manifest,
            "Celoria-Industries/infrastructure-as-code",
            PLAN_WORKFLOW_REF,
            {
                "target_account_id": "123456789012",
                "target_region": "us-east-2",
                "terraform_root": "stacks/celoria-industries/staging/foundation",
                "signing_key_arn": manifest["source"]["signing_key_arn"],
            },
        )
        self.assertEqual(source["plan_artifact_id"], 102)
        manifest["source"]["evidence_artifact_id"] = manifest["source"]["plan_artifact_id"]
        with self.assertRaisesRegex(GateError, "must be separate artifacts"):
            validate_terraform_manifest(
                manifest,
                "Celoria-Industries/infrastructure-as-code",
                PLAN_WORKFLOW_REF,
                {},
            )
        with self.assertRaisesRegex(GateError, "must be image or terraform"):
            validate_supported_kind("shell")

    def test_terraform_artifact_requires_unique_id_digest_and_run_binding(self):
        digest = "sha256:" + "1" * 64
        artifact = {
            "id": 102,
            "name": "staging-terraform-plan",
            "digest": digest,
            "expired": False,
            "workflow_run": {"id": 101},
        }

        class ArtifactApi:
            def __init__(self, artifacts):
                self.artifacts = artifacts

            def get(self, path):
                if path == "/repos/Celoria-Industries/infrastructure-as-code/actions/runs/101/artifacts?per_page=100&page=1":
                    return {"artifacts": self.artifacts, "total_count": len(self.artifacts)}
                if path.endswith("/actions/artifacts/102"):
                    return artifact
                raise AssertionError(f"Unexpected API request: {path}")

        validate_terraform_artifact(
            ArtifactApi([artifact]),
            "Celoria-Industries/infrastructure-as-code",
            102,
            "staging-terraform-plan",
            digest,
            101,
        )
        with self.assertRaisesRegex(GateError, "exactly one"):
            validate_terraform_artifact(
                ArtifactApi([artifact, dict(artifact, id=104)]),
                "Celoria-Industries/infrastructure-as-code",
                102,
                "staging-terraform-plan",
                digest,
                101,
            )


if __name__ == "__main__":
    unittest.main()
