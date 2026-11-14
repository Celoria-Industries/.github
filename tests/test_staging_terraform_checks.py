from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

from staging_check_fixtures import terraform_checks_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github/actions/staging-promotion-gate"))
from verify_promotion import GateError, PLAN_REPOSITORY, validate_terraform_source_checks


class TerraformSourceChecksTest(unittest.TestCase):
    def setUp(self):
        self.head = "a" * 40
        self.responses, self.checks = terraform_checks_fixture(PLAN_REPOSITORY, self.head)
        self.api = Mock()
        self.api.get.side_effect = self.responses.__getitem__
        prefix = f"/repos/{PLAN_REPOSITORY}"
        self.runs = self.responses[
            f"{prefix}/actions/workflows/401/runs?event=pull_request&head_sha={self.head}&per_page=100&page=1"
        ]["workflow_runs"]
        self.jobs_response = self.responses[f"{prefix}/actions/runs/302/attempts/1/jobs?per_page=100&page=1"]
        self.jobs = self.jobs_response["jobs"]
        self.tree = self.responses[f"{prefix}/git/trees/{self.head}?recursive=1"]

    def verify(self, layer="foundation"):
        validate_terraform_source_checks(self.api, PLAN_REPOSITORY, self.head, layer, self.checks)

    def test_newest_selected_run_supersedes_old_all_root_failures(self):
        self.verify()

    def test_latest_run_failure_pending_or_wrong_identity_cannot_fall_back(self):
        for field, value in (("conclusion", "failure"), ("status", "in_progress"),
                             ("head_sha", "b" * 40), ("event", "push"),
                             ("workflow_id", 999), ("path", ".github/workflows/other.yml"),
                             ("head_repository", {"full_name": "attacker/fork"})):
            with self.subTest(field=field):
                original = self.runs[0][field]
                self.runs[0][field] = value
                with self.assertRaises(GateError):
                    self.verify()
                self.runs[0][field] = original

    def test_newer_bad_run_blocks_older_success(self):
        self.runs[1].update(id=303, conclusion="failure", run_started_at="2026-11-14T00:11:00Z")
        with self.assertRaisesRegex(GateError, "Latest same-head"):
            self.verify()

    def test_newer_retry_of_older_run_cannot_hide_behind_green_newer_run_id(self):
        self.runs[1].update(run_attempt=2, status="queued", run_started_at="2026-11-14T00:11:00Z")
        with self.assertRaisesRegex(GateError, "Latest same-head"):
            self.verify()

    def test_latest_attempt_jobs_are_required(self):
        self.runs[0]["run_attempt"] = 2
        prefix = f"/repos/{PLAN_REPOSITORY}/actions/runs/302/attempts"
        self.responses[f"{prefix}/2/jobs?per_page=100&page=1"] = self.jobs_response
        self.verify()
        self.api.get.assert_any_call(f"{prefix}/2/jobs?per_page=100&page=1")

    def test_wrong_layer_or_missing_required_plan_validation_summary_blocks(self):
        with self.assertRaises(GateError):
            self.verify("nervous-system")
        for job in list(self.jobs):
            if job["name"] == "Apply merged PR plan":
                continue
            with self.subTest(missing=job["name"]):
                check = next(check for check in self.checks if check["id"] == job["id"])
                self.jobs.remove(job)
                self.checks.remove(check)
                self.jobs_response["total_count"] = len(self.jobs)
                with self.assertRaisesRegex(GateError, "required check"):
                    self.verify()
                self.jobs.append(job)
                self.checks.append(check)
                self.jobs_response["total_count"] = len(self.jobs)

    def test_jobs_must_match_exact_head_suite_app_and_success(self):
        for field, value in (("head_sha", "b" * 40), ("check_suite", {"id": 999}),
                             ("app", {"id": 7, "slug": "github-actions"}),
                             ("app", {"id": 15368, "slug": "other-app"}),
                             ("conclusion", "failure"), ("status", "queued")):
            with self.subTest(field=field):
                original = self.checks[0][field]
                self.checks[0][field] = value
                with self.assertRaises(GateError):
                    self.verify()
                self.checks[0][field] = original

    def test_unrelated_effective_failure_pending_or_wrong_app_is_not_ignored(self):
        other = {**deepcopy(self.checks[0]), "id": 2000, "name": "Security",
                 "check_suite": {"id": 999}, "conclusion": "failure"}
        self.checks.append(other)
        with self.assertRaisesRegex(GateError, "unrelated effective"):
            self.verify()
        other["name"] = self.checks[0]["name"]
        with self.assertRaisesRegex(GateError, "unrelated effective"):
            self.verify()
        other.update(conclusion=None, status="queued")
        with self.assertRaises(GateError):
            self.verify()
        other.update(conclusion="success", status="completed")
        self.verify()
        self.checks.append({**deepcopy(other), "id": 2001, "conclusion": "failure"})
        with self.assertRaises(GateError):
            self.verify()
        self.checks[-1].update(app={"id": 444, "slug": "another-app"})
        with self.assertRaises(GateError):
            self.verify()

    def test_extra_staging_root_or_incomplete_tree_rejects_selection(self):
        extra = deepcopy(self.jobs[0])
        extra.update(id=3000, name=(
            "Plan stacks/celoria-industries/staging/service-bootstrap / "
            "Plan stacks/celoria-industries/staging/service-bootstrap"))
        self.jobs.append(extra)
        self.jobs_response["total_count"] = len(self.jobs)
        self.checks.append({**extra, "app": {"id": 15368, "slug": "github-actions"},
                            "check_suite": {"id": 202}})
        with self.assertRaises(GateError):
            self.verify()
        self.jobs.remove(extra)
        self.jobs_response["total_count"] = len(self.jobs)
        self.checks.pop()
        self.tree["truncated"] = True
        with self.assertRaisesRegex(GateError, "enumerate"):
            self.verify()

    def test_nonstaging_root_cannot_be_omitted_from_required_checks(self):
        for root in ("stacks/_global/security", "stacks/_global/baseline",
                     "stacks/celoria-industries/prod/foundation"):
            with self.subTest(root=root):
                self.tree["tree"].append({"type": "blob", "path": root + "/main.tf"})
                with self.assertRaisesRegex(GateError, "required check"):
                    self.verify()
                self.tree["tree"].pop()

    def test_missing_or_unrecognized_run_evidence_fails_closed(self):
        self.runs.clear()
        runs_response = next(value for value in self.responses.values() if "workflow_runs" in value)
        runs_response["total_count"] = 0
        with self.assertRaisesRegex(GateError, "no Terraform"):
            self.verify()

    def test_run_and_job_pagination_cannot_select_an_older_success(self):
        prefix = f"/repos/{PLAN_REPOSITORY}"
        run_path = (
            f"{prefix}/actions/workflows/401/runs?event=pull_request&head_sha={self.head}&per_page=100&page="
        )
        self.responses[run_path + "1"] = {"total_count": 2, "workflow_runs": [self.runs[1]]}
        self.responses[run_path + "2"] = {"total_count": 2, "workflow_runs": [self.runs[0]]}
        job_path = f"{prefix}/actions/runs/302/attempts/1/jobs?per_page=100&page="
        self.responses[job_path + "1"] = {"total_count": len(self.jobs), "jobs": self.jobs[:5]}
        self.responses[job_path + "2"] = {"total_count": len(self.jobs), "jobs": self.jobs[5:]}
        self.verify()
        self.runs[0]["status"] = "queued"
        with self.assertRaisesRegex(GateError, "Latest same-head"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
