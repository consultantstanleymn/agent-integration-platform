"""Local adapters for the blob, queue and secret ports."""

import pytest

from aip.ports.blob import LocalBlobStore
from aip.ports.queue import InMemoryQueue
from aip.ports.secrets import EnvSecretProvider


def test_local_blob_roundtrip(tmp_path):
    store = LocalBlobStore(tmp_path)
    assert not store.exists("a/b.txt")
    store.put("a/b.txt", b"hello", "text/plain")
    assert store.exists("a/b.txt") and store.get("a/b.txt") == b"hello"
    store.delete("a/b.txt")
    assert not store.exists("a/b.txt")
    store.delete("a/b.txt")  # deleting twice is fine


@pytest.mark.parametrize("key", ["../x", "/etc/passwd", "a/../../x", "", "a\\b"])
def test_local_blob_rejects_path_escape(tmp_path, key):
    with pytest.raises(ValueError):
        LocalBlobStore(tmp_path).put(key, b"x")


def test_memory_queue_send_receive_delete():
    q = InMemoryQueue()
    q.send({"n": 1})
    [job] = q.receive()
    assert job.body == {"n": 1} and job.receive_count == 1
    q.delete(job)
    assert q.receive() == [] and q.dlq_depth() == 0


def test_memory_queue_moves_to_dlq_after_max_receives():
    q = InMemoryQueue(max_receive_count=3)
    q.send({"n": 1})
    for expected in (1, 2, 3):
        [job] = q.receive()
        assert job.receive_count == expected  # never deleted
    assert q.receive() == []
    assert q.dlq_depth() == 1


def test_memory_queue_redrive_moves_back_once():
    q = InMemoryQueue(max_receive_count=1)
    q.send({"n": 1})
    q.receive()
    assert q.dlq_depth() == 1
    assert q.redrive() == 1
    assert q.dlq_depth() == 0 and q.redrive() == 0
    [job] = q.receive()
    assert job.body == {"n": 1}
    q.delete(job)


def test_env_secret_provider(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "abc")
    assert EnvSecretProvider().get("llm_api_key") == "abc"
    with pytest.raises(KeyError):
        EnvSecretProvider().get("missing_secret_name")
