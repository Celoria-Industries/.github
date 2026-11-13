import datetime
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/actions/staging-promotion-gate"))
from consume_solo import ConsumptionError, consume  # noqa: E402
from solo_approval import SoloApprovalError, verify_comment  # noqa: E402
from verify_promotion import GateError, validate_reviewed_pr  # noqa: E402

NOW = datetime.datetime(2026, 11, 13, 17, 0, tzinfo=datetime.timezone.utc)
EXPECTED = {
    "schema_version": 1, "type": "promotion", "repository_id": 123,
    "pull_request": 42, "head_sha": "a" * 40, "target": "staging",
    "account_id": "123456789012", "region": "us-east-2",
    "manifest_sha256": "sha256:" + "b" * 64, "target_resource": "stacks/customer/staging/network",
}


class Api:
    base = "https://api.github.com"

    def __init__(self):
        self.repo_id = 123
        self.comment = {
            "id": 987, "issue_url": self.base + "/repos/celoria/iac/issues/42",
            "user": {"id": 17, "type": "User"}, "performed_via_github_app": None,
            "created_at": "2026-11-13T16:59:00Z", "updated_at": "2026-11-13T16:59:00Z",
            "body": json.dumps(EXPECTED),
        }

    def get(self, path):
        if path == "/repos/celoria/iac":
            return {"id": self.repo_id}
        if path == "/repos/celoria/iac/issues/comments/987":
            return self.comment
        raise AssertionError(path)


def ledger_env():
    return {
        "GITHUB_REPOSITORY_ID": "123", "STAGING_SOLO_SOURCE_COMMENT_ID": "986",
        "STAGING_SOLO_PROMOTION_COMMENT_ID": "987", "STAGING_SOLO_AUTHORIZER_ID": "17",
        "PROMOTION_PR_NUMBER": "42", "SOURCE_PR_NUMBER": "41", "SOURCE_HEAD_SHA": "c" * 40,
        "MANIFEST_SHA256": "b" * 64, "PROMOTION_HEAD_SHA": "a" * 40,
        "AUTHORIZED_AT": "2026-11-13T16:59:00Z", "SOURCE_AUTHORIZED_AT": "2026-11-13T16:58:00Z",
        "TARGET_RESOURCE": "stacks/customer/staging/network",
        "GITHUB_RUN_ID": "111", "GITHUB_RUN_ATTEMPT": "1",
        "STAGING_SOLO_TARGET_ACCOUNT_ID": "123456789012", "STAGING_SOLO_TARGET_REGION": "us-east-2",
        "STAGING_SOLO_LEDGER_TABLE_ARN": "arn:aws:dynamodb:us-east-2:987654321098:table/staging-ledger",
        "STAGING_SOLO_LEDGER_ROLE_ARN": "arn:aws:iam::987654321098:role/staging-ledger",
    }


class SoloApprovalTest(unittest.TestCase):
    def check(self, api):
        return verify_comment(api, "celoria/iac", 123, 987, 17, 600, EXPECTED, NOW)

    def test_exact_authorization(self):
        self.assertEqual(self.check(Api()), "2026-11-13T16:59:00Z")

    def test_rejects_identity_timestamp_and_binding_changes(self):
        cases = [
            ("wrong repo id", lambda a: setattr(a, "repo_id", 9)),
            ("wrong issue", lambda a: a.comment.update(issue_url=a.base + "/repos/celoria/iac/issues/41")),
            ("wrong author", lambda a: a.comment["user"].update(id=18)),
            ("bot", lambda a: a.comment["user"].update(type="Bot")),
            ("app", lambda a: a.comment.update(performed_via_github_app={"id": 1})),
            ("edited", lambda a: a.comment.update(updated_at="2026-11-13T17:00:00Z")),
            ("expired", lambda a: a.comment.update(created_at="2026-11-13T16:00:00Z", updated_at="2026-11-13T16:00:00Z")),
            ("future", lambda a: a.comment.update(created_at="2026-11-13T17:00:01Z", updated_at="2026-11-13T17:00:01Z")),
            ("changed head", lambda a: a.comment.update(body=json.dumps({**EXPECTED, "head_sha": "d" * 40}))),
            ("changed manifest", lambda a: a.comment.update(body=json.dumps({**EXPECTED, "manifest_sha256": "sha256:" + "d" * 64}))),
            ("changed target", lambda a: a.comment.update(body=json.dumps({**EXPECTED, "account_id": "999999999999"}))),
            ("duplicate keys", lambda a: a.comment.update(body='{"type":"source","type":"promotion"}')),
        ]
        for label, change in cases:
            with self.subTest(label=label):
                api = Api()
                change(api)
                with self.assertRaises(SoloApprovalError):
                    self.check(api)

    def test_expiry_configuration_is_bounded(self):
        for value in (0, 86401, True):
            with self.subTest(value=value), self.assertRaises(SoloApprovalError):
                verify_comment(Api(), "celoria/iac", 123, 987, 17, value, EXPECTED, NOW)

    def test_solo_source_requires_explicit_exact_authorization(self):
        source = {
            "schema_version": 1, "type": "source", "repository_id": 123,
            "pull_request": 42, "head_sha": "a" * 40, "target": "staging",
            "account_id": "123456789012", "region": "us-east-2",
        }
        api = Api()
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        api.comment.update(body=json.dumps(source), created_at=timestamp, updated_at=timestamp)
        original_get = api.get
        pr = {
            "state": "closed", "merged_at": timestamp,
            "base": {"ref": "main", "repo": {"full_name": "celoria/iac"}},
            "head": {"sha": "a" * 40, "repo": {"full_name": "celoria/iac"}},
            "user": {"login": "operator", "type": "User"},
        }
        def get(path):
            if path == "/repos/celoria/iac/pulls/42":
                return pr
            if "/check-runs?" in path:
                return {"check_runs": [{"status": "completed", "conclusion": "success"}], "total_count": 1}
            return original_get(path)
        api.get = get
        api.list = lambda path: []
        env = {
            "STAGING_APPROVAL_MODE": "solo", "GITHUB_REPOSITORY_ID": "123",
            "STAGING_SOLO_AUTHORIZER_ID": "17", "STAGING_SOLO_APPROVAL_MAX_AGE_SECONDS": "600",
            "STAGING_SOLO_SOURCE_COMMENT_ID": "987", "STAGING_SOLO_TARGET_ACCOUNT_ID": "123456789012",
            "STAGING_SOLO_TARGET_REGION": "us-east-2",
        }
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(validate_reviewed_pr(api, "celoria/iac", 42)[1], "a" * 40)
            api.comment["body"] = json.dumps({**source, "head_sha": "b" * 40})
            with self.assertRaisesRegex(GateError, "exact staging evidence"):
                validate_reviewed_pr(api, "celoria/iac", 42)
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(GateError, "human approval"):
            validate_reviewed_pr(api, "celoria/iac", 42)

    def test_single_use_and_ambiguous_failures(self):
        lock = threading.Lock()
        seen = set()

        def fake_run(args, **kwargs):
            if args[1] == "sts":
                return subprocess.CompletedProcess(args, 0, "987654321098\n", "")
            self.assertIn("attribute_not_exists(authorization_id)", args)
            item = json.loads(args[args.index("--item") + 1])
            key = item["authorization_id"]["S"]
            with lock:
                exists = key in seen
                seen.add(key)
            return subprocess.CompletedProcess(args, int(exists), "", "")

        outcomes = []
        def attempt():
            try:
                consume(ledger_env(), fake_run)
                outcomes.append("success")
            except ConsumptionError:
                outcomes.append("denied")
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["success", "denied"])
        with self.assertRaises(ConsumptionError):
            consume(ledger_env(), fake_run)

        def ambiguous(args, **kwargs):
            if args[1] == "sts":
                return subprocess.CompletedProcess(args, 0, "987654321098\n", "")
            return subprocess.CompletedProcess(args, 255, "", "timeout")
        with self.assertRaisesRegex(ConsumptionError, "ambiguous"):
            consume(ledger_env(), ambiguous)

    def test_ledger_rejects_wrong_account_before_write(self):
        env = ledger_env()
        env["STAGING_SOLO_LEDGER_ROLE_ARN"] = "arn:aws:iam::123456789012:role/ledger"
        with self.assertRaisesRegex(ConsumptionError, "same account"):
            consume(env, lambda *args, **kwargs: self.fail("must not call AWS"))


if __name__ == "__main__":
    unittest.main()
