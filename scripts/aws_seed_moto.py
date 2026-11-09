"""Create the engagement resources on an emulator endpoint (moto). For tests and demos only.

Usage: python scripts/aws_seed_moto.py --endpoint-url http://127.0.0.1:25150
Prints one JSON line with the names and URLs. It never contacts real AWS: --endpoint-url is required.
"""

import argparse
import json
import sys
from typing import Any

import boto3

DEFAULTS = {
    "bucket": "aip-engagement-docs",
    "queue": "aip-engagement-ingest",
    "dlq": "aip-engagement-ingest-dlq",
    "secret": "aip-engagement/llm-api-key",
}


def seed(endpoint_url: str, region: str = "us-east-1", redrive: bool = True) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"region_name": region, "endpoint_url": endpoint_url}
    s3 = boto3.client("s3", **kwargs)
    sqs = boto3.client("sqs", **kwargs)
    sm = boto3.client("secretsmanager", **kwargs)

    s3.create_bucket(Bucket=DEFAULTS["bucket"])
    s3.put_public_access_block(
        Bucket=DEFAULTS["bucket"],
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=DEFAULTS["bucket"],
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    dlq_url = sqs.create_queue(QueueName=DEFAULTS["dlq"])["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    attributes = {"VisibilityTimeout": "0"}
    if redrive:
        attributes["RedrivePolicy"] = json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": 3})
    queue_url = sqs.create_queue(QueueName=DEFAULTS["queue"], Attributes=attributes)["QueueUrl"]
    sm.create_secret(Name=DEFAULTS["secret"], SecretString="placeholder-not-a-real-key")
    return {
        "bucket": DEFAULTS["bucket"],
        "queue_url": queue_url,
        "dlq_url": dlq_url,
        "secret_id": DEFAULTS["secret"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-url", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--no-redrive", action="store_true", help="make a queue without a redrive policy")
    args = parser.parse_args(argv)
    print(json.dumps(seed(args.endpoint_url, args.region, redrive=not args.no_redrive)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
