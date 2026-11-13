import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile


class EvidenceError(Exception):
    pass


RECORD_KEYS = {
    "schema_version",
    "target",
    "source_repository",
    "source_commit",
    "source_pull_request_number",
    "source_pull_request_head_sha",
    "source_pull_request_merge_sha",
    "producer_run_id",
    "producer_run_attempt",
    "producer_run_head_sha",
    "producer_workflow_ref",
    "plan_artifact_id",
    "plan_artifact_name",
    "plan_artifact_digest",
    "plan_sha256",
    "target_account_id",
    "target_region",
    "terraform_root",
    "signing_key_arn",
    "signing_algorithm",
}


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def strict_record(data):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Signed Terraform evidence contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        record = json.loads(data, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError("Signed Terraform evidence is not valid UTF-8 JSON") from error
    canonical_record(record)
    return record


def canonical_record(record):
    require(isinstance(record, dict), "Signed Terraform evidence record must be an object")
    require(set(record) == RECORD_KEYS, "Signed Terraform evidence has an unexpected schema")
    require(record.get("schema_version") == 1, "Unsupported signed Terraform evidence schema")
    require(record.get("target") == "staging", "Signed Terraform evidence target must be staging")
    require(record.get("signing_algorithm") == "RSASSA_PSS_SHA_256",
            "Signed Terraform evidence must use RSASSA_PSS_SHA_256")
    require(record.get("source_commit") == record.get("source_pull_request_merge_sha"),
            "Signed Terraform evidence source commit must be the reviewed PR merge commit")
    for key in ("source_commit", "source_pull_request_head_sha", "source_pull_request_merge_sha",
                "producer_run_head_sha"):
        require(isinstance(record.get(key), str) and re.fullmatch(r"[0-9a-f]{40}", record[key]) is not None,
                f"Signed Terraform evidence {key} must be a lowercase commit SHA")
    for key in (
        "source_pull_request_number",
        "producer_run_id",
        "producer_run_attempt",
        "plan_artifact_id",
    ):
        require(type(record.get(key)) is int and record[key] > 0,
                f"Signed Terraform evidence {key} must be a positive integer")
    require(
        record.get("source_repository") == "Celoria-Industries/infrastructure-as-code",
        "Signed Terraform evidence source repository is not the infrastructure repository",
    )
    require(record.get("plan_artifact_name") == "staging-terraform-plan",
            "Signed Terraform evidence uses an unexpected plan artifact name")
    for key in ("plan_artifact_digest", "plan_sha256"):
        require(isinstance(record.get(key), str)
                and re.fullmatch(r"sha256:[0-9a-f]{64}", record[key]) is not None,
                f"Signed Terraform evidence {key} must be a SHA-256 digest")
    require(isinstance(record.get("target_account_id"), str)
            and re.fullmatch(r"[0-9]{12}", record["target_account_id"]) is not None,
            "Signed Terraform evidence target_account_id must be a 12-digit AWS account ID")
    require(isinstance(record.get("target_region"), str)
            and re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]", record["target_region"]) is not None,
            "Signed Terraform evidence target_region is invalid")
    require(
        isinstance(record.get("terraform_root"), str)
        and re.fullmatch(r"stacks/[a-z0-9][a-z0-9-]*/staging/[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*", record["terraform_root"]) is not None,
        "Signed Terraform evidence terraform_root must be a staging stack path",
    )
    require(
        isinstance(record.get("signing_key_arn"), str)
        and re.fullmatch(
            r"arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[0-9a-f-]{36}",
            record["signing_key_arn"],
        ) is not None,
        "Signed Terraform evidence signing_key_arn must be an immutable KMS key ARN",
    )
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def verify_plan_evidence(record, signature_b64, signing_algorithm, public_key_der_b64, expected):
    require(signing_algorithm == "RSASSA_PSS_SHA_256",
            "Terraform plan evidence uses an unsupported signature algorithm")
    message = canonical_record(record)
    for key, value in expected.items():
        require(record.get(key) == value, f"Signed Terraform evidence does not match {key}")
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key_der = base64.b64decode(public_key_der_b64, validate=True)
    except (TypeError, ValueError) as error:
        raise EvidenceError("Terraform evidence signature or pinned public key is invalid base64") from error

    with tempfile.TemporaryDirectory(prefix="celoria-plan-signature-") as directory:
        message_path = os.path.join(directory, "record.json")
        signature_path = os.path.join(directory, "signature.bin")
        public_key_der_path = os.path.join(directory, "public-key.der")
        public_key_path = os.path.join(directory, "public-key.pem")
        with open(message_path, "wb") as target:
            target.write(message)
        with open(signature_path, "wb") as target:
            target.write(signature)
        with open(public_key_der_path, "wb") as target:
            target.write(public_key_der)

        convert = subprocess.run(
            [
                "openssl", "pkey", "-pubin", "-inform", "DER", "-in", public_key_der_path,
                "-out", public_key_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        require(convert.returncode == 0, "Pinned KMS public key is not a valid DER SubjectPublicKeyInfo key")
        verified = subprocess.run(
            [
                "openssl", "dgst", "-sha256", "-verify", public_key_path, "-signature", signature_path,
                "-sigopt", "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-1", message_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        require(verified.returncode == 0, "Terraform plan KMS signature verification failed")


def verify_plan_bytes(record, plan_path):
    require(os.path.isfile(plan_path), "Signed Terraform plan artifact does not contain tfplan")
    with open(plan_path, "rb") as plan:
        digest = hashlib.sha256(plan.read()).hexdigest()
    require(record.get("plan_sha256") == f"sha256:{digest}",
            "Downloaded Terraform plan does not match the signed binary SHA-256")
