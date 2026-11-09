"""AWS adapters: S3 for blobs, SQS (with a dead-letter queue) for jobs, Secrets Manager for secrets.

Each adapter takes a boto3 client. Moving from an emulator to real AWS changes settings only.
"""

import json
import time
from collections.abc import Callable
from typing import Any

import boto3

from aip.config import Settings
from aip.ports.blob import check_key
from aip.ports.queue import Job


def make_client(service: str, s: Settings) -> Any:
    kwargs: dict[str, Any] = {"region_name": s.aws_region}
    if s.aws_endpoint_url:
        kwargs["endpoint_url"] = s.aws_endpoint_url
    return boto3.client(service, **kwargs)


class S3BlobStore:
    def __init__(self, bucket: str, client: Any) -> None:
        self.bucket = bucket
        self.client = client

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=check_key(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        body: bytes = self.client.get_object(Bucket=self.bucket, Key=check_key(key))["Body"].read()
        return body

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=check_key(key))

    def exists(self, key: str) -> bool:
        listing = self.client.list_objects_v2(Bucket=self.bucket, Prefix=check_key(key), MaxKeys=1)
        return any(obj["Key"] == key for obj in listing.get("Contents", []))


class SqsJobQueue:
    """The main queue has a redrive policy that points to the DLQ. AWS moves the messages."""

    def __init__(self, queue_url: str, dlq_url: str, client: Any) -> None:
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.client = client

    def send(self, body: dict[str, Any]) -> str:
        reply = self.client.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(body))
        return str(reply["MessageId"])

    def receive(self, max_messages: int = 1, wait_s: int = 0) -> list[Job]:
        reply = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=min(max_messages, 10),
            WaitTimeSeconds=wait_s,
            AttributeNames=["ApproximateReceiveCount"],
        )
        return [_job(m) for m in reply.get("Messages", [])]

    def delete(self, job: Job) -> None:
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=job.receipt)

    def dlq_depth(self) -> int:
        attrs = self.client.get_queue_attributes(
            QueueUrl=self.dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
        )
        return int(attrs["Attributes"]["ApproximateNumberOfMessages"])

    def redrive(self, max_messages: int = 10) -> int:
        """Move DLQ messages back to the main queue, one at a time. Returns the count."""
        moved = 0
        while moved < max_messages:
            reply = self.client.receive_message(
                QueueUrl=self.dlq_url, MaxNumberOfMessages=1, WaitTimeSeconds=0
            )
            messages = reply.get("Messages", [])
            if not messages:
                break
            message = messages[0]
            self.client.send_message(QueueUrl=self.queue_url, MessageBody=message["Body"])
            self.client.delete_message(QueueUrl=self.dlq_url, ReceiptHandle=message["ReceiptHandle"])
            moved += 1
        return moved


def _job(message: dict[str, Any]) -> Job:
    count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
    return Job(
        id=message["MessageId"],
        body=json.loads(message["Body"]),
        receipt=message["ReceiptHandle"],
        receive_count=count,
    )


class AwsSecretProvider:
    """Reads ``SecretString`` values. A small TTL cache avoids a call per use."""

    def __init__(
        self,
        client: Any,
        prefix: str = "",
        ttl_s: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.prefix = prefix
        self.ttl_s = ttl_s
        self.clock = clock
        self._cache: dict[str, tuple[float, str]] = {}

    def get(self, name: str) -> str:
        now = self.clock()
        hit = self._cache.get(name)
        if hit and now - hit[0] < self.ttl_s:
            return hit[1]
        value: str = self.client.get_secret_value(SecretId=f"{self.prefix}{name}")["SecretString"]
        self._cache[name] = (now, value)
        return value
