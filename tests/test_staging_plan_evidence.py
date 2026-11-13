import base64
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/actions/staging-promotion-gate"))

from plan_evidence import (  # noqa: E402
    EvidenceError,
    canonical_record,
    strict_record,
    verify_plan_bytes,
    verify_plan_evidence,
)


WORKFLOW_SHA = "1" * 40
COMMIT = "3" * 40
MERGE_SHA = COMMIT
RUN_HEAD_SHA = "4" * 40
PLAN_DIGEST = "sha256:" + "5" * 64


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
        "terraform_root": "stacks/celoria-industries/staging/foundation",
        "signing_key_arn": "arn:aws:kms:us-east-2:123456789012:key/12345678-1234-1234-1234-123456789012",
        "signing_algorithm": "RSASSA_PSS_SHA_256",
    }


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
