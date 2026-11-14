import base64
from copy import deepcopy
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_staging_plan_evidence as fixtures
from plan_evidence import canonical_record
from verify_promotion import GateError, GitHubApi, main


class TerraformVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys.cleanup)
        cls.private = Path(cls.keys.name) / "key.pem"
        cls.public = Path(cls.keys.name) / "key.der"
        cls.other_public = Path(cls.keys.name) / "other.der"
        for private, public in (
            (cls.private, cls.public), (Path(cls.keys.name) / "other.pem", cls.other_public)
        ):
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
                 "-out", str(private)], check=True, capture_output=True,
            )
            subprocess.run(
                ["openssl", "pkey", "-in", str(private), "-pubout", "-outform", "DER",
                 "-out", str(public)], check=True, capture_output=True,
            )

    def setUp(self):
        fixtures.CreatePlanEvidenceTest.setUp(self)
        record = self.record
        directory = Path(self.tempdir.name)
        self.record_path = directory / "evidence.json"
        self.record_path.write_bytes(canonical_record(record))
        signature = directory / "signature.bin"
        subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(self.private),
             "-sigopt", "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-1",
             "-out", str(signature), str(self.record_path)],
            check=True, capture_output=True,
        )
        self.signature_path = directory / "signature.b64"
        self.signature_path.write_text(base64.b64encode(signature.read_bytes()).decode())
        self.gate_output = directory / "gate-output"
        self.repository = record["source_repository"]
        self.prefix = f"/repos/{self.repository}"
        prefix = self.prefix
        self.source = {
            "repository": record["source_repository"], "commit": record["source_commit"],
            "pull_request_number": 91, "pull_request_head_sha": record["source_pull_request_head_sha"],
            "pull_request_merge_sha": record["source_pull_request_merge_sha"],
            "workflow_run_id": 101, "workflow_run_attempt": 1,
            "workflow_run_head_sha": record["producer_run_head_sha"],
            "evidence_artifact_id": 103, "evidence_artifact_name": "staging-terraform-evidence",
            "evidence_artifact_digest": "sha256:" + "6" * 64,
        }
        for field in (
            "producer_workflow_ref", "plan_artifact_id", "plan_artifact_name", "plan_artifact_digest",
            "plan_sha256", "target_account_id", "target_region", "plan_layer", "terraform_root",
            "signing_key_arn", "signing_algorithm",
        ):
            self.source[field] = record[field]
        self.manifest = {"schema_version": 1, "target": "staging", "kind": "terraform",
                         "source": self.source}
        self.manifest_bytes = json.dumps(self.manifest, separators=(",", ":")).encode()
        self.manifest_sha = hashlib.sha256(self.manifest_bytes).hexdigest()
        self.promotion_head = "7" * 40
        source_pr = self.responses[f"{prefix}/pulls/91"]
        source_pr["number"] = 91
        promotion_pr = deepcopy(source_pr)
        promotion_pr.update(number=92, changed_files=1)
        promotion_pr["head"]["sha"] = self.promotion_head
        self.responses.update({
            f"{prefix}/pulls/92": promotion_pr,
            f"{prefix}/pulls/92/reviews?per_page=100&page=1": [],
            f"{prefix}/pulls/91/reviews?per_page=100&page=1": [],
            f"{prefix}/pulls/92/files?per_page=2": [{"filename": ".github/staging-promotion.json"}],
            f"{prefix}/commits/{self.promotion_head}/check-runs?filter=latest&per_page=100&page=1": {
                "total_count": 1, "check_runs": [{"status": "completed", "conclusion": "success"}],
            },
            f"{prefix}/contents/.github/staging-promotion.json?ref={self.promotion_head}": {
                "encoding": "base64", "content": base64.b64encode(self.manifest_bytes).decode(),
            },
            prefix: {"id": 123},
        })
        self.responses[f"{prefix}/actions/runs/101/attempts/1"].update(
            status="completed", conclusion="success"
        )
        evidence_artifact = {
            **self.artifact, "id": 103, "name": self.source["evidence_artifact_name"],
            "digest": self.source["evidence_artifact_digest"],
        }
        self.responses[f"{prefix}/actions/artifacts/103"] = evidence_artifact
        self.responses[f"{prefix}/actions/runs/101/artifacts?per_page=100&page=1"] = {
            "total_count": 2, "artifacts": [self.artifact, evidence_artifact],
        }
        now = datetime.datetime.now(datetime.timezone.utc)
        for number, comment_id, age, extras, head, purpose in (
            (91, 901, 120, {"plan_layer": record["plan_layer"],
                           "terraform_root": record["terraform_root"]},
             record["source_pull_request_head_sha"], "source"),
            (92, 902, 60, {"manifest_sha256": "sha256:" + self.manifest_sha,
                          "target_resource": record["terraform_root"], "plan_layer": record["plan_layer"]},
             self.promotion_head, "promotion"),
        ):
            timestamp = (now - datetime.timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
            body = {
                "schema_version": 1, "type": purpose, "repository_id": 123,
                "pull_request": number, "head_sha": head, "target": "staging",
                "account_id": record["target_account_id"], "region": record["target_region"], **extras,
            }
            self.responses[f"{prefix}/issues/comments/{comment_id}"] = {
                "id": comment_id, "issue_url": f"https://api.github.com{prefix}/issues/{number}",
                "user": {"id": 17, "type": "User"}, "performed_via_github_app": None,
                "created_at": timestamp, "updated_at": timestamp, "body": json.dumps(body),
            }
        self.environment.update({
            "VERIFICATION_MODE": "terraform-verification", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY_ID": "123",
            "PROMOTION_PR_NUMBER": "92", "GITHUB_OUTPUT": str(self.gate_output),
            "TRUSTED_PLAN_PRODUCER_WORKFLOW": record["producer_workflow_ref"],
            "EXPECTED_MANIFEST_SHA": self.manifest_sha,
            "EVIDENCE_RECORD_PATH": str(self.record_path),
            "EVIDENCE_SIGNATURE_PATH": str(self.signature_path),
            "STAGING_PLAN_SIGNING_PUBLIC_KEY_DER_B64": base64.b64encode(self.public.read_bytes()).decode(),
            "STAGING_APPROVAL_MODE": "solo", "STAGING_SOLO_AUTHORIZER_ID": "17",
            "STAGING_SOLO_APPROVAL_MAX_AGE_SECONDS": "3600", "STAGING_SOLO_SOURCE_COMMENT_ID": "901",
            "STAGING_SOLO_PROMOTION_COMMENT_ID": "902",
            "STAGING_SOLO_TARGET_ACCOUNT_ID": record["target_account_id"],
            "STAGING_SOLO_TARGET_REGION": record["target_region"],
        })

    def run_gate(self):
        self.gate_output.unlink(missing_ok=True)
        with patch.dict(os.environ, self.environment, clear=True), \
                patch.object(GitHubApi, "get", side_effect=self.responses.__getitem__):
            main()
        return dict(line.split("=", 1) for line in self.gate_output.read_text().splitlines())

    def assert_rejected(self):
        with self.assertRaises(GateError):
            self.run_gate()
        self.assertFalse(self.gate_output.exists())

    def test_metadata_then_real_rsa_pss_and_plan_verification_with_solo_bindings(self):
        self.environment["VERIFICATION_MODE"] = "terraform-metadata"
        for key in ("PLAN_ARTIFACT_PATH", "EVIDENCE_RECORD_PATH", "EVIDENCE_SIGNATURE_PATH"):
            self.environment[key] = ""
        outputs = self.run_gate()
        self.assertNotIn("verification_result", outputs)
        self.assertEqual(outputs["manifest_sha256"], self.manifest_sha)
        self.assertEqual(outputs["producer_run_id"], "101")
        self.environment.update({
            "VERIFICATION_MODE": "terraform-verification",
            "PLAN_ARTIFACT_PATH": str(Path(self.tempdir.name) / "tfplan"),
            "EVIDENCE_RECORD_PATH": str(self.record_path),
            "EVIDENCE_SIGNATURE_PATH": str(self.signature_path),
        })
        outputs = self.run_gate()
        self.assertEqual(outputs["verification_result"], "verified")
        self.assertEqual(outputs["plan_layer"], "foundation")
        self.assertEqual(outputs["manifest_sha256"], self.manifest_sha)

    def test_missing_local_evidence_or_prior_manifest_cannot_report_success(self):
        for key in ("PLAN_ARTIFACT_PATH", "EVIDENCE_RECORD_PATH", "EVIDENCE_SIGNATURE_PATH",
                    "EXPECTED_MANIFEST_SHA"):
            with self.subTest(key=key):
                original = self.environment[key]
                self.environment[key] = ""
                self.assert_rejected()
                self.environment[key] = original

    def test_rejects_tampered_plan_record_signature_and_wrong_public_key(self):
        for path in (Path(self.environment["PLAN_ARTIFACT_PATH"]), self.record_path, self.signature_path):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(b"tampered")
                self.assert_rejected()
                path.write_bytes(original)
        self.environment["STAGING_PLAN_SIGNING_PUBLIC_KEY_DER_B64"] = base64.b64encode(
            self.other_public.read_bytes()
        ).decode()
        self.assert_rejected()

    def test_wrong_context_target_producer_or_manifest_is_rejected(self):
        for key, value in (
            ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REF", "refs/heads/feature"),
            ("GITHUB_REPOSITORY", "attacker/infrastructure-as-code"),
            ("STAGING_PLAN_TARGET_ACCOUNT_ID", "999999999999"),
            ("STAGING_PLAN_TARGET_REGION", "us-west-2"),
            ("TRUSTED_PLAN_PRODUCER_WORKFLOW", self.record["producer_workflow_ref"][:-40] + "9" * 40),
            ("EXPECTED_MANIFEST_SHA", "0" * 64),
        ):
            with self.subTest(key=key):
                original = self.environment[key]
                self.environment[key] = value
                self.assert_rejected()
                self.environment[key] = original

    def test_each_authorization_is_current_unedited_and_exact(self):
        for comment_id in (901, 902):
            comment = self.responses[f"{self.prefix}/issues/comments/{comment_id}"]
            original = deepcopy(comment)
            for changes in (
                {"updated_at": "2000-01-01T00:00:00Z"},
                {"created_at": "2000-01-01T00:00:00Z", "updated_at": "2000-01-01T00:00:00Z"},
                {"user": {"id": 18, "type": "User"}},
                {"body": json.dumps({**json.loads(original["body"]), "head_sha": "0" * 40})},
            ):
                with self.subTest(comment=comment_id, changes=changes):
                    comment.update(changes)
                    self.assert_rejected()
                    comment.clear()
                    comment.update(deepcopy(original))

    def test_independent_approval_mode_still_requires_both_reviews(self):
        self.environment["STAGING_APPROVAL_MODE"] = "independent"
        for number, head in ((91, self.record["source_pull_request_head_sha"]), (92, self.promotion_head)):
            self.assert_rejected()
            self.responses[f"{self.prefix}/pulls/{number}/reviews?per_page=100&page=1"] = [{
                "state": "APPROVED", "commit_id": head, "submitted_at": "2026-11-13T09:00:00Z",
                "user": {"login": "reviewer", "type": "User"},
            }]
        self.assertEqual(self.run_gate()["verification_result"], "verified")

    def test_failed_checks_expired_artifact_and_incorrect_attempt_remain_blocking(self):
        check = self.responses[
            f"{self.prefix}/commits/{self.promotion_head}/check-runs?filter=latest&per_page=100&page=1"
        ]["check_runs"][0]
        check["conclusion"] = "failure"
        self.assert_rejected()
        check["conclusion"] = "success"
        self.artifact["expired"] = True
        self.assert_rejected()
        self.artifact["expired"] = False
        self.responses[f"{self.prefix}/actions/runs/101/attempts/1"]["run_attempt"] = 2
        self.assert_rejected()

    def test_metadata_rejects_image_and_nonstaging_manifest_before_download(self):
        content = self.responses[
            f"{self.prefix}/contents/.github/staging-promotion.json?ref={self.promotion_head}"
        ]
        self.environment["VERIFICATION_MODE"] = "terraform-metadata"
        self.environment["EXPECTED_MANIFEST_SHA"] = ""
        for changes in ({"kind": "image"}, {"target": "production"}):
            with self.subTest(changes=changes):
                content["content"] = base64.b64encode(json.dumps({**self.manifest, **changes}).encode()).decode()
                self.assert_rejected()
