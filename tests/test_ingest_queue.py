"""Async document ingest: blob store, queue, worker, dead-letter queue and redrive."""

import json

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import select

from aip.cli import main
from aip.db import make_session_factory
from aip.models import Document
from aip.ports import Ports
from aip.ports.aws import S3BlobStore, SqsJobQueue
from aip.ports.blob import LocalBlobStore
from aip.ports.queue import InMemoryQueue
from aip.ports.secrets import EnvSecretProvider
from aip.worker import run_once

REGION = "us-east-1"
TEXT = "# Returns\n\nReturns are accepted within 60 days."


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


def _moto_ports():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="docs")
    sqs = boto3.client("sqs", region_name=REGION)
    dlq = sqs.create_queue(QueueName="dlq")["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    policy = json.dumps({"deadLetterTargetArn": arn, "maxReceiveCount": 2})
    url = sqs.create_queue(QueueName="q", Attributes={"RedrivePolicy": policy, "VisibilityTimeout": "0"})[
        "QueueUrl"
    ]
    return Ports(S3BlobStore("docs", s3), SqsJobQueue(url, dlq, sqs), EnvSecretProvider())


@pytest.fixture(params=["memory", "moto"])
def ports(request, tmp_path):
    if request.param == "memory":
        yield Ports(
            LocalBlobStore(tmp_path / "blobs"), InMemoryQueue(max_receive_count=2), EnvSecretProvider()
        )
    else:
        with mock_aws():
            yield _moto_ports()


@pytest.fixture
def app_env(env, ports):
    env.app.state.ports = ports
    env.t = env.tenant()
    env.factory = make_session_factory(env.engine)
    return env


def _queue_doc(env, name="returns.md", key=None, text=TEXT):
    headers = dict(env.t["headers"])
    if key:
        headers["Idempotency-Key"] = key
    r = env.client.post("/v1/documents/async", json={"name": name, "text": text}, headers=headers)
    assert r.status_code == 202, r.text
    return r.json()


def _docs(env):
    with env.factory() as db:
        return db.scalars(select(Document)).all()


def _drain(ports, rounds=5):
    for _ in range(rounds):
        ports.queue.receive(max_messages=10)


def test_async_ingest_then_worker_creates_document(app_env, ports):
    reply = _queue_doc(app_env)
    assert reply["status"] == "queued" and ports.blob.exists(reply["blob_key"])
    result = run_once(ports, app_env.factory, app_env.app.state.embedder)
    assert result == {"processed": 1, "failed": 0}
    assert [d.name for d in _docs(app_env)] == ["returns.md"]
    assert run_once(ports, app_env.factory, app_env.app.state.embedder) == {"processed": 0, "failed": 0}


def test_poison_message_goes_to_dlq(app_env, ports):
    reply = _queue_doc(app_env)
    ports.blob.delete(reply["blob_key"])  # the job now points to a missing blob
    for _ in range(2):
        assert run_once(ports, app_env.factory, app_env.app.state.embedder)["failed"] == 1
    _drain(ports)
    assert ports.queue.dlq_depth() == 1
    assert _docs(app_env) == []


def test_redrive_after_fix_ingests_once(app_env, ports):
    reply = _queue_doc(app_env)
    ports.blob.delete(reply["blob_key"])
    for _ in range(2):
        run_once(ports, app_env.factory, app_env.app.state.embedder)
    _drain(ports)
    assert ports.queue.dlq_depth() == 1
    ports.blob.put(reply["blob_key"], TEXT.encode(), "text/plain")  # the fix
    assert ports.queue.redrive() == 1
    assert run_once(ports, app_env.factory, app_env.app.state.embedder)["processed"] == 1
    assert len(_docs(app_env)) == 1
    assert ports.queue.dlq_depth() == 0


def test_redrive_twice_does_not_duplicate(app_env, ports):
    reply = _queue_doc(app_env, key="same-key")
    run_once(ports, app_env.factory, app_env.app.state.embedder)
    assert len(_docs(app_env)) == 1
    # The same job arrives again, as after a redrive. The idempotency key skips the work.
    ports.queue.send(
        {
            "tenant_id": app_env.t["id"],
            "blob_key": reply["blob_key"],
            "name": "returns.md",
            "idempotency_key": "same-key",
        }
    )
    assert run_once(ports, app_env.factory, app_env.app.state.embedder)["processed"] == 1
    assert len(_docs(app_env)) == 1


def test_cli_queue_depth_and_redrive(monkeypatch, capsys):
    with mock_aws():
        ports = _moto_ports()
        ports.queue.send({"x": 1})
        for _ in range(4):
            ports.queue.receive()
        assert ports.queue.dlq_depth() == 1
        monkeypatch.setenv("QUEUE_BACKEND", "sqs")
        monkeypatch.setenv("SQS_QUEUE_URL", ports.queue.queue_url)
        monkeypatch.setenv("SQS_DLQ_URL", ports.queue.dlq_url)
        assert main(["queue", "depth"]) == 0
        assert json.loads(capsys.readouterr().out) == {"dlq_depth": 1}
        assert main(["queue", "redrive", "--max", "5"]) == 0
        assert json.loads(capsys.readouterr().out) == {"redriven": 1}
        assert ports.queue.dlq_depth() == 0
