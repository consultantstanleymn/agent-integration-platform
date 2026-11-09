"""Static checks on the HCL in infra/aws-engagement. Nothing here runs Terraform."""

from pathlib import Path

import hcl2
import pytest

INFRA = Path(__file__).resolve().parents[1] / "infra" / "aws-engagement"


def _clean(value):
    """Newer python-hcl2 keeps the quotes of labels and strings. Remove them."""
    if isinstance(value, dict):
        return {_clean(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _load() -> dict[str, list]:
    merged: dict[str, list] = {}
    for path in sorted(INFRA.glob("*.tf")):
        with path.open() as handle:
            data = _clean(hcl2.load(handle))
        for key, value in data.items():
            merged.setdefault(key, []).extend(value)
    return merged


def _resources(kind: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for block in _load().get("resource", []):
        for rtype, named in block.items():
            if rtype == kind:
                out.update(named)
    return out


def _policy_docs() -> dict[str, list[dict]]:
    docs: dict[str, list[dict]] = {}
    for block in _load().get("data", []):
        for dtype, named in block.items():
            if dtype == "aws_iam_policy_document":
                for name, body in named.items():
                    docs[name] = body.get("statement", [])
    return docs


def test_hcl_parses():
    names = {p.name for p in INFRA.glob("*.tf")}
    assert {"main.tf", "variables.tf", "outputs.tf", "versions.tf"} <= names
    assert _load()["resource"]


def test_public_access_block_all_true():
    blocks = _resources("aws_s3_bucket_public_access_block")
    assert blocks
    for body in blocks.values():
        for flag in (
            "block_public_acls",
            "block_public_policy",
            "ignore_public_acls",
            "restrict_public_buckets",
        ):
            assert body[flag] is True, flag


def test_queue_has_redrive_policy():
    queues = _resources("aws_sqs_queue")
    assert set(queues) == {"ingest", "ingest_dlq"}
    policy = queues["ingest"]["redrive_policy"]
    assert "deadLetterTargetArn" in policy and "aws_sqs_queue.ingest_dlq.arn" in policy
    assert "maxReceiveCount" in policy
    assert "redrive_policy" not in queues["ingest_dlq"]


def test_iam_policy_has_no_wildcards():
    docs = _policy_docs()
    assert {"app_s3_bucket", "app_s3_objects", "app_sqs", "app_secret"} <= set(docs)
    for name, statements in docs.items():
        for statement in statements:
            for action in statement.get("actions", []):
                assert "*" not in action, f"{name}: wildcard action {action}"
            for resource in statement.get("resources", []):
                text = str(resource)
                assert text != "*" and not text.endswith(":*"), f"{name}: wildcard resource {text}"
    # The object statement may end in /* only on the bucket ARN, which limits it to that bucket.
    objects = docs["app_s3_objects"][0]["resources"]
    assert all("aws_s3_bucket.docs.arn" in r for r in objects)


def test_budget_exists_with_notification():
    budgets = _resources("aws_budgets_budget")
    assert len(budgets) == 1
    body = next(iter(budgets.values()))
    assert body["time_unit"] == "MONTHLY"
    assert "monthly_budget_usd" in body["limit_amount"]
    notification = body["notification"][0]
    assert notification["threshold"] == 80
    assert notification["notification_type"] == "ACTUAL"


@pytest.mark.parametrize("name", ["project", "owner"])
def test_default_tags_present(name):
    provider = _load()["provider"][0]["aws"]
    tags = provider["default_tags"][0]["tags"]
    assert name in tags
