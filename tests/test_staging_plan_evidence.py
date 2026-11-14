import base64
import hashlib
import json
import os
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/actions/staging-promotion-gate"))

from plan_evidence import (  # noqa: E402
    EvidenceError,
    canonical_record,
    strict_record,
    verify_plan_bytes,
    verify_plan_evidence,
)
from verify_promotion import GitHubApi  # noqa: E402
from verify_promotion import GateError, validate_terraform_manifest  # noqa: E402


WORKFLOW_SHA = "1" * 40
COMMIT = "3" * 40
MERGE_SHA = COMMIT
RUN_HEAD_SHA = "4" * 40
PLAN_DIGEST = "sha256:" + "5" * 64
PROVIDER_UPLOAD_DIGEST = "d0579dc3ec01103aaa2d07cd397e7c332802ef520ea1bc24883671c765af3720"
PLAN_WORKFLOW = (ROOT / ".github/workflows/staging-terraform-plan.yml").read_text()


def producer_step_script(name):
    step = PLAN_WORKFLOW.split(f"      - name: {name}\n", 1)[1].split("\n      - name:", 1)[0]
    script = step.split("        run: |\n", 1)[1]
    lines = []
    for line in script.splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines)


def evidence_record():
    return {
        "schema_version": 1,
        "target": "staging",
        "source_repository": "Celoria-Industries/infrastructure-as-code",
        "source_commit": COMMIT,
        "source_pull_request_number": 91,
        "source_pull_request_head_sha": "2" * 40,
        "source_pull_request_merge_sha": MERGE_SHA,
        "producer_run_id": 101,
        "producer_run_attempt": 1,
        "producer_run_head_sha": RUN_HEAD_SHA,
        "producer_workflow_ref": (
            "Celoria-Industries/.github/.github/workflows/staging-terraform-plan.yml@"
            + WORKFLOW_SHA
        ),
        "plan_artifact_id": 102,
        "plan_artifact_name": "staging-terraform-plan",
        "plan_artifact_digest": PLAN_DIGEST,
        "plan_sha256": PLAN_DIGEST,
        "target_account_id": "123456789012",
        "target_region": "us-east-2",
        "plan_layer": "foundation",
        "terraform_root": "stacks/celoria-industries/staging/foundation",
        "signing_key_arn": "arn:aws:kms:us-east-2:123456789012:key/12345678-1234-1234-1234-123456789012",
        "signing_algorithm": "RSASSA_PSS_SHA_256",
    }


class CreatePlanEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        directory = Path(self.tempdir.name)
        plan = directory / "tfplan"
        plan.write_bytes(b"synthetic plan fixture; no Terraform or state")
        self.output = directory / "evidence"
        self.record = evidence_record()
        self.record["plan_sha256"] = "sha256:" + hashlib.sha256(plan.read_bytes()).hexdigest()
        record = self.record
        repository = record["source_repository"]
        head = record["source_pull_request_head_sha"]
        root = record["terraform_root"]
        prefix = f"/repos/{repository}"
        self.artifact = {
            "id": 102, "name": "staging-terraform-plan", "digest": PLAN_DIGEST,
            "expired": False, "workflow_run": {"id": 101},
        }
        self.responses = {
            f"{prefix}/pulls/91": {
                "state": "closed", "merged_at": "2026-11-13T10:00:00Z",
                "base": {"ref": "main", "repo": {"full_name": repository}},
                "head": {"sha": head, "repo": {"full_name": repository}},
                "user": {"login": "author", "type": "User"},
                "merge_commit_sha": MERGE_SHA,
                "labels": [{"name": "staging-plan:foundation"}],
            },
            f"{prefix}/pulls/91/reviews?per_page=100&page=1": [{
                "state": "APPROVED", "commit_id": head,
                "submitted_at": "2026-11-13T09:00:00Z",
                "user": {"login": "reviewer", "type": "User"},
            }],
            f"{prefix}/commits/{head}/check-runs?filter=latest&per_page=100&page=1": {
                "total_count": 1, "check_runs": [{
                    "name": f"Plan {root} / Plan {root}",
                    "status": "completed", "conclusion": "success",
                }],
            },
            f"{prefix}/actions/runs/101/attempts/1": {
                "status": "in_progress", "event": "workflow_dispatch", "head_branch": "main",
                "head_sha": RUN_HEAD_SHA, "run_attempt": 1,
                "path": ".github/workflows/staging-terraform-plan.yml",
            },
            f"{prefix}/compare/{MERGE_SHA}...{RUN_HEAD_SHA}": {"status": "ahead"},
            f"{prefix}/contents/.github/workflows/staging-terraform-plan.yml?ref={RUN_HEAD_SHA}": {
                "encoding": "base64",
                "content": base64.b64encode(
                    f"jobs:\n  plan:\n    uses: {record['producer_workflow_ref']}\n".encode()
                ).decode(),
            },
            f"{prefix}/actions/runs/101/artifacts?per_page=100&page=1": {
                "total_count": 1, "artifacts": [self.artifact],
            },
            f"{prefix}/actions/artifacts/102": self.artifact,
        }
        self.environment = {
            "GH_TOKEN": "synthetic-test-token",
            "GITHUB_REPOSITORY": repository,
            "SOURCE_PR_NUMBER": "91", "STAGING_PLAN_LAYER": "foundation",
            "SOURCE_PR_HEAD_SHA": head, "SOURCE_PR_MERGE_SHA": MERGE_SHA,
            "GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": RUN_HEAD_SHA,
            "STAGING_TRUSTED_PLAN_WORKFLOW": record["producer_workflow_ref"],
            "PLAN_ARTIFACT_ID": "102", "PLAN_ARTIFACT_DIGEST": PLAN_DIGEST,
            "PLAN_ARTIFACT_PATH": str(plan),
            "STAGING_PLAN_TARGET_ACCOUNT_ID": record["target_account_id"],
            "STAGING_PLAN_TARGET_REGION": record["target_region"],
            "STAGING_PLAN_SIGNING_KEY_ARN": record["signing_key_arn"],
            "EVIDENCE_OUTPUT_DIRECTORY": str(self.output),
        }

    def run_entrypoint(self):
        with patch.dict(os.environ, self.environment, clear=True), \
                patch.object(GitHubApi, "get", side_effect=self.responses.__getitem__):
            runpy.run_path(
                str(ROOT / ".github/actions/staging-promotion-gate/create_plan_evidence.py"),
                run_name="__main__",
            )

    def test_entrypoint_emits_exact_canonical_evidence(self):
        self.run_entrypoint()
        self.assertEqual((self.output / "evidence.json").read_bytes(), canonical_record(self.record))
        self.assertEqual(strict_record((self.output / "evidence.json").read_bytes()), self.record)

    def test_entrypoint_rejects_artifact_digest_substitution_before_output(self):
        result, output = self.normalize_upload("Normalize saved-plan upload digest", PROVIDER_UPLOAD_DIGEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.environment["PLAN_ARTIFACT_DIGEST"] = output.removeprefix("digest=").strip()
        self.artifact["digest"] = "sha256:" + "9" * 64
        with self.assertRaises(SystemExit) as result:
            self.run_entrypoint()
        self.assertEqual(result.exception.code, 1)
        self.assertFalse(self.output.exists())

    def normalize_upload(self, step, digest):
        output = Path(self.tempdir.name) / "step-output"
        output.write_text("")
        result = subprocess.run(
            ["bash", "-c", producer_step_script(step)],
            env={**os.environ, "UPLOAD_DIGEST": digest, "GITHUB_OUTPUT": str(output)},
            capture_output=True, text=True,
        )
        return result, output.read_text()

    def test_native_upload_digest_flows_through_evidence_outputs_and_manifest(self):
        result, output = self.normalize_upload("Normalize saved-plan upload digest", PROVIDER_UPLOAD_DIGEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        digest = output.removeprefix("digest=").strip()
        self.assertEqual(digest, "sha256:" + PROVIDER_UPLOAD_DIGEST)
        self.environment["PLAN_ARTIFACT_DIGEST"] = digest
        self.artifact["digest"] = digest
        self.record["plan_artifact_digest"] = digest
        self.run_entrypoint()
        evidence = strict_record((self.output / "evidence.json").read_bytes())
        self.assertEqual(evidence, self.record)

        evidence_upload_digest = hashlib.sha256(b"synthetic evidence archive").hexdigest()
        result, output = self.normalize_upload("Normalize signed-evidence upload digest", evidence_upload_digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence_digest = output.removeprefix("digest=").strip()
        directory = Path(self.tempdir.name)
        downloaded = directory / "staging-plan-artifacts"
        downloaded.mkdir()
        (downloaded / "tfplan").write_bytes(Path(self.environment["PLAN_ARTIFACT_PATH"]).read_bytes())
        summary = directory / "summary"
        result = subprocess.run(
            ["bash", "-c", producer_step_script("Publish promotion-manifest source fields")],
            env={
                **os.environ, **self.environment,
                "RUNNER_TEMP": str(directory), "GITHUB_STEP_SUMMARY": str(summary),
                "PLAN_ARTIFACT_DIGEST": digest, "EVIDENCE_ARTIFACT_ID": "103",
                "EVIDENCE_ARTIFACT_DIGEST": evidence_digest,
                "TARGET_ACCOUNT_ID": evidence["target_account_id"],
                "TARGET_REGION": evidence["target_region"], "TERRAFORM_ROOT": evidence["terraform_root"],
                "PLAN_LAYER": evidence["plan_layer"], "SIGNING_KEY_ARN": evidence["signing_key_arn"],
                "TRUSTED_PLAN_WORKFLOW": evidence["producer_workflow_ref"],
            },
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((directory / "staging-promotion-manifest.json").read_bytes())
        source = validate_terraform_manifest(
            manifest, evidence["source_repository"], evidence["producer_workflow_ref"],
            {key: evidence[key] for key in ("target_account_id", "target_region", "signing_key_arn")},
        )
        self.assertEqual(source["plan_artifact_digest"], evidence["plan_artifact_digest"])
        self.assertEqual(source["plan_sha256"], evidence["plan_sha256"])
        self.assertEqual(source["evidence_artifact_digest"], "sha256:" + evidence_upload_digest)
        self.assertEqual(source["terraform_root"], evidence["terraform_root"])
        for field in ("plan_artifact_digest", "evidence_artifact_digest"):
            with self.subTest(field=field):
                source[field] = source[field].removeprefix("sha256:")
                with self.assertRaises(GateError):
                    validate_terraform_manifest(
                        manifest, evidence["source_repository"], evidence["producer_workflow_ref"], {}
                    )
                source[field] = "sha256:" + source[field]
        self.assertIn("plan_artifact_digest: ${{ steps.normalize-plan.outputs.digest }}", PLAN_WORKFLOW)
        self.assertIn("evidence_artifact_digest: ${{ steps.normalize-evidence.outputs.digest }}", PLAN_WORKFLOW)
        self.assertIn("value: ${{ jobs.plan.outputs.plan_artifact_digest }}", PLAN_WORKFLOW)
        self.assertIn("value: ${{ jobs.sign.outputs.evidence_artifact_digest }}", PLAN_WORKFLOW)
        self.assertIn("plan-artifact-digest: ${{ needs.plan.outputs.plan_artifact_digest }}", PLAN_WORKFLOW)
        self.assertIn("EVIDENCE_ARTIFACT_DIGEST: ${{ steps.normalize-evidence.outputs.digest }}", PLAN_WORKFLOW)
        self.assertIn("plan-artifact-path: ${{ runner.temp }}/staging-plan-artifacts/tfplan", PLAN_WORKFLOW)

    def test_upload_boundaries_reject_malformed_or_already_prefixed_digests(self):
        for step in ("Normalize saved-plan upload digest", "Normalize signed-evidence upload digest"):
            for value in ("", "a" * 63, "a" * 65, "G" * 64, PROVIDER_UPLOAD_DIGEST.upper(),
                          "sha256:" + PROVIDER_UPLOAD_DIGEST, PROVIDER_UPLOAD_DIGEST + "\ninjected=1"):
                with self.subTest(step=step, value=value):
                    result, output = self.normalize_upload(step, value)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output, "")


class StagingPlanEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.tempdir.name)
        cls.private_key = cls.directory / "private.pem"
        cls.public_der = cls.directory / "public.der"
        cls.message = cls.directory / "record.json"
        cls.signature = cls.directory / "signature.bin"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
             "-out", str(cls.private_key)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["openssl", "pkey", "-in", str(cls.private_key), "-pubout", "-outform", "DER",
             "-out", str(cls.public_der)],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        self.record = evidence_record()
        self.message.write_bytes(canonical_record(self.record))
        subprocess.run(
            [
                "openssl", "dgst", "-sha256", "-sign", str(self.private_key),
                "-sigopt", "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-1",
                "-out", str(self.signature), str(self.message),
            ],
            check=True,
            capture_output=True,
        )
        self.signature_b64 = base64.b64encode(self.signature.read_bytes()).decode()
        self.public_key_b64 = base64.b64encode(self.public_der.read_bytes()).decode()

    def test_verifies_real_rsa_pss_signature_and_exact_bound_fields(self):
        verify_plan_evidence(
            self.record,
            self.signature_b64,
            "RSASSA_PSS_SHA_256",
            self.public_key_b64,
            {
                "target": "staging",
                "source_commit": COMMIT,
                "producer_workflow_ref": self.record["producer_workflow_ref"],
                "plan_artifact_id": 102,
            },
        )

    def test_rejects_tampered_signature_and_record(self):
        altered_signature = base64.b64encode(b"tampered signature").decode()
        with self.assertRaisesRegex(EvidenceError, "signature verification failed"):
            verify_plan_evidence(
                self.record, altered_signature, "RSASSA_PSS_SHA_256", self.public_key_b64, {}
            )
        tampered = dict(self.record, plan_sha256="sha256:" + "9" * 64)
        with self.assertRaisesRegex(EvidenceError, "signature verification failed"):
            verify_plan_evidence(
                tampered, self.signature_b64, "RSASSA_PSS_SHA_256", self.public_key_b64, {}
            )

    def test_rejects_wrong_key_or_algorithm(self):
        alternate_private = self.directory / "alternate-private.pem"
        alternate_public = self.directory / "alternate-public.der"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
             "-out", str(alternate_private)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["openssl", "pkey", "-in", str(alternate_private), "-pubout", "-outform", "DER",
             "-out", str(alternate_public)],
            check=True,
            capture_output=True,
        )
        with self.assertRaisesRegex(EvidenceError, "signature verification failed"):
            verify_plan_evidence(
                self.record,
                self.signature_b64,
                "RSASSA_PSS_SHA_256",
                base64.b64encode(alternate_public.read_bytes()).decode(),
                {},
            )
        with self.assertRaisesRegex(EvidenceError, "unsupported signature algorithm"):
            verify_plan_evidence(self.record, self.signature_b64, "ECDSA_SHA_256", self.public_key_b64, {})

    def test_requires_rsa_3072_pinned_public_key(self):
        private_key = self.directory / "rsa-2048.pem"
        public_der = self.directory / "rsa-2048.der"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
             "-out", str(private_key)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["openssl", "pkey", "-in", str(private_key), "-pubout", "-outform", "DER",
             "-out", str(public_der)],
            check=True,
            capture_output=True,
        )
        with self.assertRaisesRegex(EvidenceError, "RSA-3072"):
            verify_plan_evidence(
                self.record,
                self.signature_b64,
                "RSASSA_PSS_SHA_256",
                base64.b64encode(public_der.read_bytes()).decode(),
                {},
            )

    def test_rejects_duplicate_record_keys(self):
        with self.assertRaisesRegex(EvidenceError, "duplicate JSON keys"):
            strict_record(b'{"target":"staging","target":"production"}')

    def test_layer_and_root_must_match_exact_allowlist(self):
        for layer, root in (
            ("nervous-system", "stacks/celoria-industries/staging/foundation"),
            ("foundation", "stacks/celoria-industries/staging/nervous-system"),
            ("unknown", "stacks/celoria-industries/staging/unknown"),
        ):
            with self.subTest(layer=layer, root=root), self.assertRaises(EvidenceError):
                canonical_record({**self.record, "plan_layer": layer, "terraform_root": root})

    def test_rejects_changed_target_and_stale_producer(self):
        with self.assertRaisesRegex(EvidenceError, "does not match target"):
            verify_plan_evidence(
                self.record,
                self.signature_b64,
                "RSASSA_PSS_SHA_256",
                self.public_key_b64,
                {"target": "production"},
            )
        with self.assertRaisesRegex(EvidenceError, "does not match producer_workflow_ref"):
            verify_plan_evidence(
                self.record,
                self.signature_b64,
                "RSASSA_PSS_SHA_256",
                self.public_key_b64,
                {"producer_workflow_ref": "Celoria-Industries/.github/.github/workflows/plan.yml@main"},
            )

    def test_binds_saved_plan_bytes(self):
        plan = self.directory / "tfplan"
        plan.write_bytes(b"the exact saved plan")
        digest = hashlib.sha256(plan.read_bytes()).hexdigest()
        self.record["plan_sha256"] = "sha256:" + digest
        verify_plan_bytes(self.record, str(plan))
        plan.write_bytes(b"a different plan")
        with self.assertRaisesRegex(EvidenceError, "does not match the signed binary"):
            verify_plan_bytes(self.record, str(plan))


if __name__ == "__main__":
    unittest.main()
