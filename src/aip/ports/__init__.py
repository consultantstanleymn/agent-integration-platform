"""Ports for blob storage, job queues and secrets. Adapters are chosen by settings."""

from dataclasses import dataclass
from pathlib import Path

from aip.config import Settings
from aip.ports.blob import BlobStore, LocalBlobStore
from aip.ports.queue import InMemoryQueue, JobQueue
from aip.ports.secrets import EnvSecretProvider, SecretProvider


@dataclass
class Ports:
    blob: BlobStore
    queue: JobQueue
    secrets: SecretProvider


def build_ports(s: Settings) -> Ports:
    blob: BlobStore
    queue: JobQueue
    secrets: SecretProvider
    if s.blob_backend == "s3" or s.queue_backend == "sqs" or s.secret_backend == "aws":
        from aip.ports.aws import AwsSecretProvider, S3BlobStore, SqsJobQueue, make_client

        blob = (
            S3BlobStore(s.s3_bucket, make_client("s3", s))
            if s.blob_backend == "s3"
            else LocalBlobStore(Path(s.blob_root))
        )
        queue = (
            SqsJobQueue(s.sqs_queue_url, s.sqs_dlq_url, make_client("sqs", s))
            if s.queue_backend == "sqs"
            else InMemoryQueue()
        )
        secrets = (
            AwsSecretProvider(make_client("secretsmanager", s), s.secret_prefix)
            if s.secret_backend == "aws"
            else EnvSecretProvider()
        )
        return Ports(blob, queue, secrets)
    return Ports(LocalBlobStore(Path(s.blob_root)), InMemoryQueue(), EnvSecretProvider())
