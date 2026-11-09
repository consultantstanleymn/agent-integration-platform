"""The cloud verifier and teardown, proven against a moto server on port 25150."""

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest
from moto.server import ThreadedMotoServer

ROOT = Path(__file__).resolve().parents[1]
PORT = 25150
ENDPOINT = f"http://127.0.0.1:{PORT}"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


seed_mod = _load("aws_seed_moto")
verify_mod = _load("aws_verify")
teardown_mod = _load("aws_teardown")
export_mod = _load("aws_policy_export")


@pytest.fixture(scope="module")
def server():
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            pytest.skip(f"port {PORT} is in use")
    srv = ThreadedMotoServer(port=PORT)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def _reset():
    import requests

    requests.post(f"{ENDPOINT}/moto-api/reset", timeout=10)


@pytest.fixture
def resources(server, tmp_path):
    _reset()
    return tmp_path, None


def _args(names, policy_file, *extra):
    return [
        "--endpoint-url",
        ENDPOINT,
        "--bucket",
        names["bucket"],
        "--queue-url",
        names["queue_url"],
        "--dlq-url",
        names["dlq_url"],
        "--secret-id",
        names["secret_id"],
        *(["--policy-file", str(policy_file)] if policy_file else []),
        *extra,
    ]


def _policy(tmp_path, names):
    policy = export_mod.export(
        names["bucket"],
        names["queue_url"].rsplit("/", 1)[-1],
        names["dlq_url"].rsplit("/", 1)[-1],
        names["secret_id"],
    )
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    return path


def test_verifier_passes_on_seeded_moto(resources, capsys):
    tmp_path, _ = resources
    names = seed_mod.seed(ENDPOINT)
    assert verify_mod.main(_args(names, _policy(tmp_path, names))) == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 7 and "FAIL" not in out


def test_verifier_fails_without_redrive_policy(resources, capsys):
    tmp_path, _ = resources
    names = seed_mod.seed(ENDPOINT, redrive=False)
    assert verify_mod.main(_args(names, _policy(tmp_path, names))) == 1
    assert "FAIL queue redrive policy targets the DLQ" in capsys.readouterr().out


def test_teardown_dry_run_changes_nothing(resources, capsys):
    tmp_path, _ = resources
    names = seed_mod.seed(ENDPOINT)
    assert teardown_mod.main(_args(names, None)) == 0
    assert "DRY-RUN" in capsys.readouterr().out
    assert verify_mod.main(_args(names, _policy(tmp_path, names))) == 0


def test_teardown_then_expect_empty_passes(resources, capsys):
    tmp_path, _ = resources
    names = seed_mod.seed(ENDPOINT)
    assert verify_mod.main(_args(names, None, "--expect-empty")) == 1  # resources exist
    assert teardown_mod.main(_args(names, None, "--yes")) == 0
    capsys.readouterr()
    assert verify_mod.main(_args(names, None, "--expect-empty")) == 0
    assert capsys.readouterr().out.count("PASS") == 4
