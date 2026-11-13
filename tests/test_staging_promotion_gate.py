import unittest

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/actions/staging-promotion-gate"))

from verify_promotion import (  # noqa: E402
    GateError,
    validate_image_manifest,
    validate_promotion_trigger,
    validate_reviewed_pr,
    validate_source_image_run,
    validate_supported_kind,
)


HEAD_SHA = "a" * 40
SOURCE_SHA = "b" * 40
PRODUCER_SHA = "c" * 40
IMAGE_WORKFLOW_REF = (
    "Celoria-Industries/.github/.github/workflows/container-image.yml@" + PRODUCER_SHA
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
            "path": ".github/workflows/container-image.yml",
        }

        class SourceApi:
            def __init__(self, wrapper):
                self.wrapper = wrapper

            def get(self, path):
                if path == "/repos/celoria/service/actions/runs/123/attempts/1":
                    return run
                if path == f"/repos/celoria/service/contents/.github/workflows/container-image.yml?ref={SOURCE_SHA}":
                    import base64
                    return {
                        "encoding": "base64",
                        "content": base64.b64encode(self.wrapper.encode()).decode(),
                    }
                raise AssertionError(f"Unexpected API request: {path}")

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

    def test_terraform_is_explicitly_disabled_without_private_artifact_provenance(self):
        with self.assertRaisesRegex(GateError, "GitHub Team plans cannot attest private-repository artifacts"):
            validate_supported_kind("terraform")


if __name__ == "__main__":
    unittest.main()
