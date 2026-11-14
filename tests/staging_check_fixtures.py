from copy import deepcopy


def terraform_checks_fixture(repository, head):
    root = "stacks/celoria-industries/staging/foundation"
    dev_roots = [f"stacks/celoria-industries/dev/{layer}"
                 for layer in ("foundation", "nervous-system", "service-bootstrap")]
    names = ["Discover Terraform roots", "Terraform validation", "Report PR plan status"]
    for candidate in [root, *dev_roots]:
        names.extend((f"Plan {candidate} / Validate Terraform caller",
                      f"Plan {candidate} / Plan {candidate}"))
    names.append("Apply merged PR plan")
    checks = [
        {"id": 1000 + index, "name": name, "head_sha": head, "status": "completed",
         "conclusion": "skipped" if name == "Apply merged PR plan" else "success",
         "app": {"id": 15368, "slug": "github-actions"}, "check_suite": {"id": 202}}
        for index, name in enumerate(names)
    ]
    old_checks = [
        {**deepcopy(check), "id": check["id"] - 100, "check_suite": {"id": 201},
         "conclusion": "cancelled"}
        for check in checks
    ]
    old_checks.append({
        **deepcopy(old_checks[-1]), "id": 950,
        "name": ("Plan stacks/celoria-industries/staging/service-bootstrap / "
                 "Plan stacks/celoria-industries/staging/service-bootstrap"),
        "conclusion": "failure",
    })
    run = {
        "id": 302, "check_suite_id": 202, "workflow_id": 401, "run_attempt": 1,
        "head_sha": head, "event": "pull_request", "path": ".github/workflows/terraform.yml",
        "status": "completed", "conclusion": "success",
        "run_started_at": "2026-11-14T00:10:39Z",
        "repository": {"full_name": repository}, "head_repository": {"full_name": repository},
    }
    old_run = {**deepcopy(run), "id": 301, "check_suite_id": 201, "conclusion": "cancelled",
               "run_started_at": "2026-11-14T00:09:44Z"}
    prefix = f"/repos/{repository}"
    jobs = [{key: value for key, value in check.items() if key not in ("app", "check_suite")}
            for check in checks]
    responses = {
        f"{prefix}/actions/workflows/terraform.yml": {"id": 401, "path": ".github/workflows/terraform.yml"},
        f"{prefix}/actions/workflows/401/runs?event=pull_request&head_sha={head}&per_page=100&page=1":
            {"total_count": 2, "workflow_runs": [run, old_run]},
        f"{prefix}/actions/runs/302/attempts/1/jobs?per_page=100&page=1":
            {"total_count": len(jobs), "jobs": jobs},
        f"{prefix}/git/trees/{head}?recursive=1": {
            "truncated": False,
            "tree": [{"type": "blob", "path": candidate + "/main.tf"}
                     for candidate in [*dev_roots, root,
                                       "stacks/celoria-industries/staging/nervous-system",
                                       "stacks/celoria-industries/staging/service-bootstrap",
                                       "stacks/celoria-industries/dev/baseline",
                                       "stacks/_global/github"]],
        },
    }
    return responses, checks + old_checks
