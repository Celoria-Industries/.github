import json
import os
import re
import subprocess
import sys


class ConsumptionError(Exception):
    pass


def consume(env, run=subprocess.run):
    def field(name, pattern):
        value = env.get(name, "")
        if not re.fullmatch(pattern, value):
            raise ConsumptionError(f"Invalid {name}")
        return value

    repository_id = field("GITHUB_REPOSITORY_ID", r"[1-9][0-9]*")
    source_id = field("STAGING_SOLO_SOURCE_COMMENT_ID", r"[1-9][0-9]*")
    comment_id = field("STAGING_SOLO_PROMOTION_COMMENT_ID", r"[1-9][0-9]*")
    author_id = field("STAGING_SOLO_AUTHORIZER_ID", r"[1-9][0-9]*")
    pr_number = field("PROMOTION_PR_NUMBER", r"[1-9][0-9]*")
    source_pr = field("SOURCE_PR_NUMBER", r"[1-9][0-9]*")
    source_head = field("SOURCE_HEAD_SHA", r"[0-9a-f]{40}")
    manifest_sha = field("MANIFEST_SHA256", r"[0-9a-f]{64}")
    head_sha = field("PROMOTION_HEAD_SHA", r"[0-9a-f]{40}")
    authorized_at = field("AUTHORIZED_AT", r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
    source_authorized_at = field("SOURCE_AUTHORIZED_AT", r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
    resource = field("TARGET_RESOURCE", r"[A-Za-z0-9._/-]{1,512}")
    run_id = field("GITHUB_RUN_ID", r"[1-9][0-9]*")
    attempt = field("GITHUB_RUN_ATTEMPT", r"[1-9][0-9]*")
    account = field("STAGING_SOLO_TARGET_ACCOUNT_ID", r"[0-9]{12}")
    region = field("STAGING_SOLO_TARGET_REGION", r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]")
    table_arn = field("STAGING_SOLO_LEDGER_TABLE_ARN", r"arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]{3,255}")
    role_arn = field("STAGING_SOLO_LEDGER_ROLE_ARN", r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+")
    ledger_account = table_arn.split(":")[4]
    if role_arn.split(":")[4] != ledger_account:
        raise ConsumptionError("Ledger role and table must belong to the same account")
    ledger_region = table_arn.split(":")[3]
    identity = run(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text", "--no-cli-pager"],
        text=True, capture_output=True, check=False,
    )
    if identity.returncode != 0 or identity.stdout.strip() != ledger_account:
        raise ConsumptionError("Unable to verify the ledger AWS account")
    fields = {
        "authorization_id": f"{repository_id}:{comment_id}",
        "repository_id": repository_id,
        "source_comment_id": source_id,
        "promotion_comment_id": comment_id,
        "author_id": author_id,
        "promotion_pr": pr_number,
        "source_pr": source_pr,
        "source_head_sha": source_head,
        "promotion_head_sha": head_sha,
        "manifest_sha256": manifest_sha,
        "authorized_at": authorized_at,
        "source_authorized_at": source_authorized_at,
        "target_account_id": account,
        "target_region": region,
        "target_resource": resource,
        "workflow_run_id": run_id,
        "workflow_run_attempt": attempt,
    }
    item = {key: {"S": value} for key, value in fields.items()}
    result = run(
        ["aws", "dynamodb", "put-item", "--region", ledger_region, "--table-name", table_arn,
         "--item", json.dumps(item, separators=(",", ":")),
         "--condition-expression", "attribute_not_exists(authorization_id)", "--no-cli-pager"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise ConsumptionError("Authorization consumption failed or is ambiguous; use a new authorization after investigation")


if __name__ == "__main__":
    try:
        consume(os.environ)
    except (ConsumptionError, OSError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
