import datetime
import json
import re


class SoloApprovalError(Exception):
    pass


def verify_comment(api, repository, repository_id, comment_id, author_id, max_age, expected, now=None):
    if not (type(repository_id) is int and repository_id > 0
            and type(comment_id) is int and comment_id > 0
            and type(author_id) is int and author_id > 0
            and type(max_age) is int and 1 <= max_age <= 86400):
        raise SoloApprovalError("Invalid solo authorization configuration")
    if (api.get(f"/repos/{repository}") or {}).get("id") != repository_id:
        raise SoloApprovalError("Repository ID does not match the authorization")
    comment = api.get(f"/repos/{repository}/issues/comments/{comment_id}")
    issue_url = f"{api.base}/repos/{repository}/issues/{expected['pull_request']}"
    if (comment.get("id") != comment_id or comment.get("issue_url") != issue_url
            or (comment.get("user") or {}).get("id") != author_id
            or (comment.get("user") or {}).get("type") != "User"
            or "performed_via_github_app" not in comment
            or comment["performed_via_github_app"] is not None):
        raise SoloApprovalError("Authorization comment identity or PR does not match")
    created = comment.get("created_at")
    if not isinstance(created, str) or created != comment.get("updated_at"):
        raise SoloApprovalError("Authorization comment is edited or lacks timestamps")
    try:
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", created):
            raise ValueError("unexpected timestamp")
        timestamp = datetime.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError as error:
        raise SoloApprovalError("Invalid authorization timestamp") from error
    now = now or datetime.datetime.now(datetime.timezone.utc)
    elapsed = (now - timestamp).total_seconds()
    if elapsed < 0 or elapsed > max_age:
        raise SoloApprovalError("Authorization comment is expired or from the future")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SoloApprovalError("Authorization comment contains duplicate keys")
            result[key] = value
        return result

    try:
        body = json.loads(comment.get("body", ""), object_pairs_hook=unique_pairs)
    except (TypeError, ValueError) as error:
        raise SoloApprovalError("Authorization comment is not valid JSON") from error
    if type(body) is not dict or set(body) != set(expected):
        raise SoloApprovalError("Authorization comment schema does not match")
    if any(type(body[key]) is not type(value) or body[key] != value for key, value in expected.items()):
        raise SoloApprovalError("Authorization comment does not match the exact staging evidence")
    return created
