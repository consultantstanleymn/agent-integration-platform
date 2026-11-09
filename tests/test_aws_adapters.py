"""S3, SQS and Secrets Manager adapters, tested against moto (an AWS emulator)."""

import json

import boto3
import pytest
from moto import mock_aws

from aip.config import Settings
from aip.ports import Ports, build_ports
from aip.ports.aws import AwsSecretProvider, S3BlobStore, SqsJobQueue
from aip.ports.queue import InMemoryQueue

REGION = "us-east-1"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")  # fake values for the emulator
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


def _queues(max_receive=2):
    sqs = boto3.client("sqs", region_name=REGION)
    dlq_url = sqs.create_queue(QueueName="jobs-dlq")["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    policy = json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": max_receive})
    url = sqs.create_queue(QueueName="jobs", Attributes={"RedrivePolicy": policy, "VisibilityTimeout": "0"})[
        "QueueUrl"
    ]
    return sqs, url, dlq_url


@mock_aws
def test_s3_roundtrip_and_delete():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="docs")
    store = S3BlobStore("docs", s3)
    store.put("tenants/1/a.txt", b"hello", "text/plain")
    assert store.exists("tenants/1/a.txt") and store.get("tenants/1/a.txt") == b"hello"
    store.delete("tenants/1/a.txt")
    assert not store.exists("tenants/1/a.txt")
    with pytest.raises(ValueError):
        store.put("../x", b"no")


@mock_aws
def test_sqs_send_receive_delete():
    sqs, url, dlq = _queues()
    q = SqsJobQueue(url, dlq, sqs)
    q.send({"n": 1})
    [job] = q.receive()
    assert job.body == {"n": 1} and job.receive_count == 1
    q.delete(job)
    assert q.receive() == [] and q.dlq_depth() == 0


@mock_aws
def test_sqs_redrive_policy_moves_to_dlq():
    sqs, url, dlq = _queues(max_receive=2)
    q = SqsJobQueue(url, dlq, sqs)
    q.send({"n": 1})
    for _ in range(3):
        q.receive()  # received, never deleted, visibility timeout 0
    assert q.dlq_depth() == 1


@mock_aws
def test_sqs_redrive_returns_count_and_empties_dlq():
    sqs, url, dlq = _queues(max_receive=1)
    q = SqsJobQueue(url, dlq, sqs)
    for n in range(3):
        q.send({"n": n})
    for _ in range(4):
        q.receive(max_messages=10)
    assert q.dlq_depth() == 3
    assert q.redrive(max_messages=10) == 3
    assert q.dlq_depth() == 0
    assert sorted(j.body["n"] for j in q.receive(max_messages=10)) == [0, 1, 2]


@mock_aws
def test_secret_provider_reads_secret_string():
    sm = boto3.client("secretsmanager", region_name=REGION)
    sm.create_secret(Name="aip/llm-key", SecretString="s3cret")
    now = [0.0]
    provider = AwsSecretProvider(sm, prefix="aip/", ttl_s=10, clock=lambda: now[0])
    assert provider.get("llm-key") == "s3cret"
    sm.put_secret_value(SecretId="aip/llm-key", SecretString="rotated")
    assert provider.get("llm-key") == "s3cret"  # served from the cache
    now[0] = 11
    assert provider.get("llm-key") == "rotated"


@mock_aws
def test_build_ports_selects_aws_backends():
    s = Settings(
        blob_backend="s3",
        queue_backend="sqs",
        secret_backend="aws",
        s3_bucket="docs",
        sqs_queue_url="http://q",
        sqs_dlq_url="http://d",
        aws_region=REGION,
    )
    ports = build_ports(s)
    assert isinstance(ports, Ports)
    assert isinstance(ports.blob, S3BlobStore) and isinstance(ports.queue, SqsJobQueue)
    assert isinstance(ports.secrets, AwsSecretProvider)
    local = build_ports(Settings())
    assert isinstance(local.queue, InMemoryQueue)
