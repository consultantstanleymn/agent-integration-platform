"""Verify the AWS resources of the engagement: S3, SQS with a DLQ, Secrets Manager and the IAM policy.

Prints one line per check, PASS or FAIL. Exits 1 on any FAIL.
With --expect-empty it passes only when the bucket, both queues and the secret are gone.
Run against an emulator (--endpoint-url) or against real AWS (leave --endpoint-url out).
Authored, not run against real AWS: only moto was used.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aip.ports.aws import SqsJobQueue  # noqa: E402

Check = tuple[str, Callable[[], str | None]]  # a check returns None on success, else a reason


def _queue_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def run_checks(checks: list[Check]) -> bool:
    ok = True
    for name, fn in checks:
        try:
            problem = fn()
        except Exception as exc:  # noqa: BLE001  a failing call is a failed check
            problem = f"{type(exc).__name__}: {exc}"
        if problem is None:
            print(f"PASS {name}")
        else:
            print(f"FAIL {name}: {problem}")
            ok = False
    return ok


def verify(args: argparse.Namespace) -> bool:
    kwargs: dict[str, Any] = {"region_name": args.region}
    if args.endpoint_url:
        kwargs["endpoint_url"] = args.endpoint_url
    s3 = boto3.client("s3", **kwargs)
    sqs = boto3.client("sqs", **kwargs)
    sm = boto3.client("secretsmanager", **kwargs)
    queue = SqsJobQueue(args.queue_url, args.dlq_url, sqs)

    def public_access_blocked() -> str | None:
        cfg = s3.get_public_access_block(Bucket=args.bucket)["PublicAccessBlockConfiguration"]
        off = [k for k, v in cfg.items() if v is not True]
        return f"not blocked: {off}" if off or len(cfg) < 4 else None

    def bucket_encrypted() -> str | None:
        rules = s3.get_bucket_encryption(Bucket=args.bucket)["ServerSideEncryptionConfiguration"]["Rules"]
        return None if rules else "no encryption rule"

    def queue_has_redrive() -> str | None:
        attrs = sqs.get_queue_attributes(QueueUrl=args.queue_url, AttributeNames=["RedrivePolicy", "All"])
        raw = attrs["Attributes"].get("RedrivePolicy")
        if not raw:
            return "no RedrivePolicy"
        dlq_arn = sqs.get_queue_attributes(QueueUrl=args.dlq_url, AttributeNames=["QueueArn"])["Attributes"][
            "QueueArn"
        ]
        return None if json.loads(raw)["deadLetterTargetArn"] == dlq_arn else "policy targets another queue"

    def message_flows() -> str | None:
        queue.send({"verify": "ping"})
        jobs = queue.receive(max_messages=1)
        if not jobs or jobs[0].body != {"verify": "ping"}:
            return "message did not arrive"
        queue.delete(jobs[0])
        return None

    def dlq_message_redriven_once() -> str | None:
        sqs.send_message(QueueUrl=args.dlq_url, MessageBody=json.dumps({"verify": "redrive"}))
        if queue.redrive(max_messages=1) != 1:
            return "redrive did not move one message"
        copies = queue.receive(max_messages=10)
        for job in copies:
            queue.delete(job)
        return None if len(copies) == 1 else f"main queue has {len(copies)} copies"

    def secret_exists() -> str | None:
        sm.describe_secret(SecretId=args.secret_id)
        return None

    def policy_limited() -> str | None:
        policy = json.loads(Path(args.policy_file).read_text())
        allowed = {
            f"arn:aws:s3:::{args.bucket}",
            f"arn:aws:s3:::{args.bucket}/*",
        }
        names = {_queue_name(args.queue_url), _queue_name(args.dlq_url)}
        for st in policy["Statement"]:
            for action in st["Action"]:
                if "*" in action:
                    return f"wildcard action {action}"
            for res in st["Resource"]:
                if res in allowed:
                    continue
                if res.startswith("arn:aws:sqs:") and res.rsplit(":", 1)[-1] in names:
                    continue
                if res.startswith("arn:aws:secretsmanager:") and f":secret:{args.secret_id}" in res:
                    continue
                return f"resource outside the three: {res}"
        return None

    return run_checks(
        [
            ("bucket blocks public access", public_access_blocked),
            ("bucket has default encryption", bucket_encrypted),
            ("queue redrive policy targets the DLQ", queue_has_redrive),
            ("one message flows through the queue", message_flows),
            ("one DLQ message is redriven once", dlq_message_redriven_once),
            ("secret exists", secret_exists),
            ("IAM policy allows only the three resources", policy_limited),
        ]
    )


def expect_empty(args: argparse.Namespace) -> bool:
    kwargs: dict[str, Any] = {"region_name": args.region}
    if args.endpoint_url:
        kwargs["endpoint_url"] = args.endpoint_url
    s3 = boto3.client("s3", **kwargs)
    sqs = boto3.client("sqs", **kwargs)
    sm = boto3.client("secretsmanager", **kwargs)

    def gone(call: Callable[[], Any]) -> Callable[[], str | None]:
        def check() -> str | None:
            try:
                call()
            except ClientError:
                return None
            return "still exists"

        return check

    return run_checks(
        [
            ("bucket is gone", gone(lambda: s3.head_bucket(Bucket=args.bucket))),
            ("queue is gone", gone(lambda: sqs.get_queue_url(QueueName=_queue_name(args.queue_url)))),
            ("DLQ is gone", gone(lambda: sqs.get_queue_url(QueueName=_queue_name(args.dlq_url)))),
            ("secret is gone", gone(lambda: sm.describe_secret(SecretId=args.secret_id))),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--endpoint-url", default="")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--dlq-url", required=True)
    parser.add_argument("--secret-id", required=True)
    parser.add_argument("--policy-file", default="")
    parser.add_argument("--expect-empty", action="store_true")
    args = parser.parse_args(argv)
    if args.expect_empty:
        return 0 if expect_empty(args) else 1
    if not args.policy_file:
        parser.error("--policy-file is required unless --expect-empty is set")
    return 0 if verify(args) else 1


if __name__ == "__main__":
    sys.exit(main())
