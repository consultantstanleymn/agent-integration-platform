"""Export the IAM policy from the Terraform files as plain JSON with resolved resource ARNs.

The verifier reads this file. ARNs are built from the names, so the check works with no AWS call.
Usage: python scripts/aws_policy_export.py --bucket B --queue Q --dlq D --secret S > policy.json
"""

import argparse
import json
import sys
from pathlib import Path

import hcl2

INFRA = Path(__file__).resolve().parents[1] / "infra" / "aws-engagement"
POLICY_DOCS = ("app_s3_bucket", "app_s3_objects", "app_sqs", "app_secret")


def _unquote(value: object) -> object:
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def export(bucket: str, queue: str, dlq: str, secret: str, region: str = "us-east-1") -> dict[str, object]:
    arns = {
        "${aws_s3_bucket.docs.arn}": f"arn:aws:s3:::{bucket}",
        "${aws_s3_bucket.docs.arn}/*": f"arn:aws:s3:::{bucket}/*",
        "${aws_sqs_queue.ingest.arn}": f"arn:aws:sqs:{region}:*:{queue}",
        "${aws_sqs_queue.ingest_dlq.arn}": f"arn:aws:sqs:{region}:*:{dlq}",
        "${aws_secretsmanager_secret.llm_api_key.arn}": (
            f"arn:aws:secretsmanager:{region}:*:secret:{secret}-*"
        ),
    }
    statements: list[dict[str, object]] = []
    with (INFRA / "main.tf").open() as handle:
        data = hcl2.load(handle)
    for block in data["data"]:
        for dtype, named in block.items():
            if _unquote(dtype) != "aws_iam_policy_document":
                continue
            for name, body in named.items():
                if _unquote(name) not in POLICY_DOCS:
                    continue
                for st in body["statement"]:
                    resources = []
                    for r in st["resources"]:
                        key = str(_unquote(r))
                        resources.append(arns.get(key, key))
                    statements.append(
                        {
                            "Effect": _unquote(st["effect"]),
                            "Action": [_unquote(a) for a in st["actions"]],
                            "Resource": resources,
                        }
                    )
    return {"Version": "2012-10-17", "Statement": statements}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--dlq", required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args(argv)
    print(json.dumps(export(args.bucket, args.queue, args.dlq, args.secret, args.region), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
