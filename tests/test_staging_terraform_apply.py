import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/staging-terraform-apply.yml").read_text()
JOBS = {
    name: body for name, body in re.findall(
        r"(?ms)^  (consume|apply):\n(.*?)(?=^  [a-z]+:\n|\Z)", WORKFLOW
    )
}
FAKE_API = """
gh() {
  [[ "${API_FAIL:-false}" == "false" ]] || return 1
  case "$*" in
    *users/*) printf '%s' "$API_USER" ;;
    *branches/main) printf '%s' "$API_BRANCH" ;;
    *deployment-branch-policies) printf '%s' "$API_POLICIES" ;;
    *environments/*) printf '%s' "$API_ENVIRONMENT" ;;
    *) return 1 ;;
  esac
}
"""


def step(job, name):
    return JOBS[job].split(f"      - name: {name}\n", 1)[1].split("\n      - name:", 1)[0]


def script(job, name):
    body = step(job, name).split("        run: |\n", 1)[1]
    return "\n".join(line[10:] for line in body.splitlines())


def environment():
    return {
        **os.environ,
        "REPOSITORY": "Celoria-Industries/infrastructure-as-code",
        "EVENT": "workflow_dispatch", "REF": "refs/heads/main", "ATTEMPT": "1",
        "ACTOR": "operator", "ACTOR_ID": "17", "TRIGGERING_ACTOR": "operator",
        "AUTHORIZER_ID": "17", "MODE": "solo", "ENABLED": "true",
        "CONFIRMATION": "apply-staging-foundation", "MANIFEST_SHA": "a" * 64,
        "GITHUB_RUN_ID": "111", "CONSUME_RESULT": "success",
        "RECEIPT": "111:1:" + "a" * 64,
        "NOT_BEFORE": "2000-01-01T00:00:00Z",
        "CONSUMED_NOT_BEFORE": "2000-01-01T00:00:00Z",
        "API_USER": json.dumps({"type": "User", "id": 17}),
        "API_BRANCH": json.dumps({"protected": True}),
        "API_ENVIRONMENT": json.dumps({"deployment_branch_policy": {
            "custom_branch_policies": True, "protected_branches": False,
        }}),
        "API_POLICIES": json.dumps([{"branch_policies": [{"name": "main", "type": "branch"}]}]),
        "VERIFIED": "verified", "SOURCE_AT": "2000-01-01T00:00:01Z",
        "PROMOTION_AT": "2000-01-01T00:00:02Z",
        "KIND": "terraform", "LAYER": "foundation",
        "ROOT": "stacks/celoria-industries/staging/foundation",
    }


class FoundationApplyWorkflowTest(unittest.TestCase):
    def run_step(self, job, name, changes=None, prefix=""):
        return subprocess.run(
            ["bash", "-c", prefix + script(job, name)],
            env={**environment(), **(changes or {})}, capture_output=True, text=True, check=False,
        )

    def assert_stopped(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_explicit_human_main_dispatch_with_protected_environments(self):
        names = {
            "consume": "Require explicit human main dispatch and protected environments",
            "apply": "Require this dispatch and consumption receipt",
        }
        cases = [
            {"REPOSITORY": "attacker/infrastructure-as-code"},
            {"EVENT": "push"}, {"EVENT": "pull_request"}, {"EVENT": "workflow_call"},
            {"REF": "refs/heads/feature"}, {"REF": "refs/tags/main"},
            {"ATTEMPT": "2"}, {"ATTEMPT": ""}, {"ATTEMPT": "01"},
            {"ENABLED": ""}, {"ENABLED": "false"}, {"MODE": "independent"},
            {"CONFIRMATION": ""}, {"CONFIRMATION": "apply"},
            {"MANIFEST_SHA": ""}, {"MANIFEST_SHA": "A" * 64},
            {"MANIFEST_SHA": "a" * 63}, {"AUTHORIZER_ID": ""},
            {"ACTOR_ID": "18"}, {"TRIGGERING_ACTOR": "different-operator"},
            {"API_USER": '{"type":"Bot","id":17}'},
            {"API_USER": '{"type":"User","id":18}'},
            {"API_BRANCH": '{"protected":false}'},
            {"API_ENVIRONMENT": "{}"},
            {"API_ENVIRONMENT": '{"deployment_branch_policy":{"protected_branches":true}}'},
            {"API_POLICIES": '[{"branch_policies":[]}]'},
            {"API_POLICIES": '[{"branch_policies":[{"name":"*","type":"branch"}]}]'},
            {"API_POLICIES": '[{"branch_policies":[{"name":"main","type":"tag"}]}]'},
            {"API_POLICIES": '[{"branch_policies":[{"name":"main","type":"branch"},{"name":"dev","type":"branch"}]}]'},
            {"API_FAIL": "true"}, {"API_USER": "malformed JSON"},
        ]
        for job, name in names.items():
            with self.subTest(job=job, allowed=True):
                result = self.run_step(job, name, prefix=FAKE_API)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for changes in cases:
                with self.subTest(job=job, changes=changes):
                    self.assert_stopped(self.run_step(job, name, changes, FAKE_API))

    def test_consumption_receipt_cannot_be_missing_reused_or_from_another_attempt(self):
        for changes in (
            {"RECEIPT": ""}, {"RECEIPT": "110:1:" + "a" * 64},
            {"RECEIPT": "111:2:" + "a" * 64}, {"RECEIPT": "111:1:" + "b" * 64},
            {"CONSUME_RESULT": "failure"}, {"CONSUME_RESULT": "skipped"},
            {"NOT_BEFORE": ""}, {"CONSUMED_NOT_BEFORE": "1999-01-01T00:00:00Z"},
        ):
            with self.subTest(changes=changes):
                self.assert_stopped(self.run_step(
                    "apply", "Require this dispatch and consumption receipt", changes, FAKE_API
                ))

    def test_old_read_only_authorizations_and_invalid_cutoffs_stop_both_jobs(self):
        for job, name in (
            ("consume", "Require new apply authorizations after the operator cutoff"),
            ("apply", "Require fresh foundation acceptance before writer setup"),
        ):
            result = self.run_step(job, name)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for changes in (
                {"VERIFIED": ""}, {"VERIFIED": "metadata"},
                {"NOT_BEFORE": ""}, {"NOT_BEFORE": "not-a-date"},
                {"NOT_BEFORE": "2100-01-01T00:00:00Z"},
                {"SOURCE_AT": "2000-01-01T00:00:00Z"},
                {"SOURCE_AT": "1999-12-31T23:59:59Z"},
                {"PROMOTION_AT": "1999-12-31T23:59:59Z"},
                {"SOURCE_AT": "2000-01-01T00:00:03Z"},
                {"PROMOTION_AT": "2100-01-01T00:00:00Z"},
            ):
                with self.subTest(job=job, changes=changes):
                    self.assert_stopped(self.run_step(job, name, changes))

    def test_only_exact_foundation_can_reach_ledger_or_writer(self):
        for job, name in (
            ("consume", "Reject every non-foundation target"),
            ("apply", "Require fresh foundation acceptance before writer setup"),
        ):
            for changes in (
                {"KIND": "image"}, {"LAYER": "nervous-system"}, {"LAYER": "service-bootstrap"},
                {"ROOT": "stacks/celoria-industries/dev/foundation"},
                {"ROOT": "stacks/celoria-industries/production/foundation"},
                {"ROOT": "stacks/celoria-industries/staging/foundation/../nervous-system"},
            ):
                with self.subTest(job=job, changes=changes):
                    self.assert_stopped(self.run_step(job, name, changes))

    def test_writer_and_ledger_configurations_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "output")
            for job, name, valid, invalid in (
                ("consume", "Validate isolated ledger configuration", {
                    "ACCOUNT": "987654321098", "REGION": "us-east-2",
                    "ROLE": "arn:aws:iam::987654321098:role/ledger",
                    "TABLE": "arn:aws:dynamodb:us-east-2:987654321098:table/staging-ledger",
                }, ({"ROLE": ""}, {"ACCOUNT": "123456789012"}, {"REGION": "us-west-2"},
                    {"TABLE": "arn:aws:dynamodb:us-east-2:123456789012:table/staging-ledger"})),
                ("apply", "Validate fixed staging writer configuration", {
                    "STATE_ROLE": "arn:aws:iam::987654321098:role/state",
                    "DEPLOY_ROLE": "arn:aws:iam::123456789012:role/deploy",
                    "STATE_BUCKET": "bootstrap-state", "STATE_REGION": "us-east-2",
                    "TARGET_ACCOUNT": "123456789012", "TARGET_REGION": "us-east-2",
                }, ({"STATE_ROLE": ""}, {"STATE_ROLE": "arn:aws:iam::123456789012:role/state"},
                    {"DEPLOY_ROLE": "arn:aws:iam::987654321098:role/deploy"},
                    {"STATE_BUCKET": ""}, {"STATE_REGION": ""}, {"TARGET_REGION": ""},
                    {"TARGET_ACCOUNT": ""})),
            ):
                valid["GITHUB_OUTPUT"] = output
                result = self.run_step(job, name, valid)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for changes in invalid:
                    with self.subTest(job=job, changes=changes):
                        self.assert_stopped(self.run_step(job, name, {**valid, **changes}))

    def test_account_failures_never_invoke_terraform_and_valid_path_applies_saved_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "tfplan"
            plan.write_bytes(b"signed fixture")
            log = Path(directory) / "terraform-log"
            env = {
                "RUNNER_TEMP": directory, "STATE_ACCOUNT": "987654321098",
                "DEPLOY_ROLE": "arn:aws:iam::123456789012:role/deploy",
                "TARGET_ACCOUNT": "123456789012", "TARGET_REGION": "us-east-2",
                "STATE_BUCKET": "bootstrap-state", "STATE_REGION": "us-east-2",
                "PLAN_PATH": str(plan), "GITHUB_STEP_SUMMARY": str(Path(directory) / "summary"),
                "TERRAFORM_LOG": str(log), "STATE_IDENTITY": "987654321098",
                "DEPLOY_IDENTITY": "123456789012",
            }
            prefix = """
            aws() {
              if [[ "$*" == *--profile* ]]; then
                printf '%s' "$DEPLOY_IDENTITY"
              else
                printf '%s' "$STATE_IDENTITY"
              fi
            }
            terraform() { printf '%s\\n' "$*" >> "$TERRAFORM_LOG"; }
            """
            name = "Verify account chain and apply only the signed saved plan"
            for changes in (
                {"STATE_IDENTITY": "123456789012"}, {"DEPLOY_IDENTITY": "987654321098"},
                {"STATE_IDENTITY": ""}, {"PLAN_PATH": str(plan) + "-missing"},
            ):
                with self.subTest(changes=changes):
                    self.assert_stopped(self.run_step("apply", name, {**env, **changes}, prefix))
                    self.assertFalse(log.exists())
            result = self.run_step("apply", name, env, prefix)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lines = log.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn(" init -input=false -lockfile=readonly ", lines[0])
            self.assertIn("key=stacks/celoria-industries/staging/foundation/terraform.tfstate", lines[0])
            self.assertEqual(lines[1], "-chdir=stacks/celoria-industries/staging/foundation"
                             f" apply -input=false -lock-timeout=10m {plan}")
            self.assertNotIn(" plan ", log.read_text())

    def test_verification_and_consumption_are_mandatory_predecessors_to_credentials(self):
        self.assertEqual(set(JOBS), {"consume", "apply"})
        self.assertIn("    needs: consume\n", JOBS["apply"])
        self.assertIn("    environment: staging-authorization\n", JOBS["consume"])
        self.assertIn("    environment: staging-foundation-apply\n", JOBS["apply"])
        self.assertNotIn("workflow_dispatch:", WORKFLOW)
        self.assertNotIn("continue-on-error", WORKFLOW)
        self.assertNotIn("always()", WORKFLOW)
        self.assertNotIn("default:", WORKFLOW)
        self.assertIn("  cancel-in-progress: false\n", WORKFLOW)
        for job, body in JOBS.items():
            with self.subTest(job=job):
                # No step-level condition can turn a mandatory gate into a skip.
                self.assertNotIn("    if:", body)
                for permission in ("actions: read", "checks: read", "contents: read",
                                   "pull-requests: read", "id-token: write"):
                    self.assertIn(permission, body.split("    steps:\n", 1)[0])
                self.assertNotIn(": write", body.replace("id-token: write", ""))
                for uses in re.findall(r"uses: ([^\n]+)", body):
                    self.assertRegex(uses, r"@[0-9a-f]{40}$")
                credential_index = body.index("uses: aws-actions/configure-aws-credentials@")
                signature_index = body.index("verification-mode: terraform-verification")
                self.assertLess(signature_index, credential_index)
                self.assertIn("expected-manifest-sha: ${{ inputs.expected-manifest-sha256 }}",
                              body[:credential_index])
                self.assertIn("plan-signing-public-key-der-b64: ${{ vars.STAGING_PLAN_SIGNING_PUBLIC_KEY_DER_B64 }}",
                              body[:credential_index])
                downloads = re.findall(r"uses: actions/download-artifact@.*?(?=\n      - name:)", body, re.S)
                self.assertEqual(len(downloads), 2)
                for download in downloads:
                    self.assertIn("digest-mismatch: error", download)
                    self.assertIn("repository: ${{ github.repository }}", download)
                    self.assertNotIn("name:", download)
        consume = JOBS["consume"]
        self.assertLess(consume.index("verification-mode: terraform-verification"),
                        consume.index("operation: consume-solo"))
        self.assertLess(consume.index("operation: consume-solo"), consume.index("id: receipt"))
        self.assertIn("verified-manifest-sha: ${{ steps.verified.outputs.manifest-sha256 }}", consume)
        self.assertNotIn("terraform -", consume)
        self.assertNotIn("checkout@", consume)
        self.assertNotIn("LEDGER_ROLE_ARN", JOBS["apply"])
        self.assertLess(JOBS["apply"].index("uses: hashicorp/setup-terraform@"),
                        JOBS["apply"].index("verification-mode: terraform-verification"))


if __name__ == "__main__":
    unittest.main()
