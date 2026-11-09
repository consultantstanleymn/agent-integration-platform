"""Record and replay provider, cassettes and the replay gate."""

import json
import shutil
from pathlib import Path

import pytest

from aip.cli import main
from aip.providers.base import Message, ProviderError
from aip.providers.fake import FakeProvider
from aip.providers.replay import RecordReplayProvider, request_key

ROOT = Path(__file__).resolve().parents[1]
MSGS = [Message(role="system", content="CLASSIFY now"), Message(role="user", content="refund please")]


def test_replay_returns_stored_response(tmp_path):
    RecordReplayProvider(FakeProvider(), tmp_path, "record", synthetic=True).chat(MSGS, [])
    replay = RecordReplayProvider(None, tmp_path, "replay")
    reply = replay.chat(MSGS, [])
    assert json.loads(reply.content)["category"] == "refund"
    stored = json.loads((tmp_path / f"{request_key(MSGS, [])}.json").read_text())
    assert stored["synthetic"] is True and stored["provider"] == "fake"


def test_replay_missing_cassette_raises(tmp_path):
    with pytest.raises(ProviderError, match="cassette missing"):
        RecordReplayProvider(None, tmp_path, "replay").chat(MSGS, [])


def test_record_blocked_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AIP_ALLOW_RECORD", raising=False)

    class Real:
        name = "openai_compat"

    with pytest.raises(ProviderError, match="AIP_ALLOW_RECORD"):
        RecordReplayProvider(Real(), tmp_path, "record")  # type: ignore[arg-type]


def test_record_blocked_with_fake_inner(tmp_path, monkeypatch):
    monkeypatch.setenv("AIP_ALLOW_RECORD", "1")
    with pytest.raises(ProviderError, match="not the fake"):
        RecordReplayProvider(FakeProvider(), tmp_path, "record")


def test_case_suite_passes_in_replay_mode(capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert main(["eval", "run", "--suite", "cases", "--replay", "--min-pass-rate", "0.95"]) == 0
    assert "Pass rate: 62/62" in capsys.readouterr().out


def test_corrupted_cassette_fails_gate(tmp_path, monkeypatch, capsys):
    copy = tmp_path / "cassettes"
    shutil.copytree(ROOT / "evals" / "cassettes", copy)
    victim = sorted(copy.glob("*.json"))[0]
    victim.write_text(victim.read_text()[:40])
    monkeypatch.chdir(ROOT)
    code = main(["eval", "run", "--suite", "cases", "--replay", "--cassettes", str(copy)])
    assert code == 1
    assert "corrupted cassette" in capsys.readouterr().err
