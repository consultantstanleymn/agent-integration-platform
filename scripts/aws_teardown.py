"""Delete every engagement resource: bucket (with all versions), both queues and the secret.

Dry run by default. Pass --yes to delete. Run scripts/aws_verify.py --expect-empty afterwards.
Authored, not run against real AWS: only moto was used.
"""

import argparse
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError


def _queue_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--endpoint-url", default="")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--dlq-url", required=True)
    parser.add_argument("--secret-id", required=True)
    parser.add_argument("--yes", action="store_true", help="delete for real")
    args = parser.parse_args(argv)

    kwargs: dict[str, Any] = {"region_name": args.region}
    if args.endpoint_url:
        kwargs["endpoint_url"] = args.endpoint_url
    s3 = boto3.client("s3", **kwargs)
    sqs = boto3.client("sqs", **kwargs)
    sm = boto3.client("secretsmanager", **kwargs)
    mode = "DELETE" if args.yes else "DRY-RUN would delete"

    print(f"{mode} bucket {args.bucket} (all object versions)")
    if args.yes:
        try:
            paginator = s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=args.bucket):
                for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
                    s3.delete_object(Bucket=args.bucket, Key=item["Key"], VersionId=item["VersionId"])
            s3.delete_bucket(Bucket=args.bucket)
        except ClientError as exc:
            print(f"bucket already gone or not deletable: {exc.response['Error']['Code']}")
    for url in (args.queue_url, args.dlq_url):
        print(f"{mode} queue {_queue_name(url)}")
        if args.yes:
            try:
                sqs.delete_queue(QueueUrl=url)
            except ClientError as exc:
                print(f"queue already gone: {exc.response['Error']['Code']}")
    print(f"{mode} secret {args.secret_id}")
    if args.yes:
        try:
            sm.delete_secret(SecretId=args.secret_id, ForceDeleteWithoutRecovery=True)
        except ClientError as exc:
            print(f"secret already gone: {exc.response['Error']['Code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
