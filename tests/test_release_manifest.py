"""Release manifest and acceptance gate."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rm = _load("release_manifest")
gate = _load("acceptance_gate")
DIGEST = "localhost/agent-integration-platform@sha256:" + "a" * 64


def _write(tmp_path, digest=DIGEST):
    out = tmp_path / "manifest.json"
    assert rm.main(["--image-digest", digest, "--allow-dirty", "--out", str(out)]) == 0
    return out


def _eval_json(tmp_path, manifest, rate=1.0, cases_hash=None):
    path = tmp_path / "eval.json"
    data = json.loads(manifest.read_text())
    path.write_text(
        json.dumps({"pass_rate": rate, "eval_set_sha256": cases_hash or data["eval_set_sha256"]["cases"]})
    )
    return path


def test_manifest_has_all_fields(tmp_path):
    data = json.loads(_write(tmp_path).read_text())
    for field in gate.FIELDS:
        assert data[field], field
    assert "@sha256:" in data["image"]
    assert data["alembic_head"] == rm.alembic_head() != ""
    assert set(data["eval_set_sha256"]) == {"cases", "testset"}


def test_config_hash_ignores_secrets(monkeypatch):
    from aip.config import Settings

    base = rm.config_hash(Settings())
    changed_secret = rm.config_hash(Settings(admin_token="another-secret", openai_api_key="k"))
    assert base == changed_secret
    assert rm.config_hash(Settings(approval_limit_minor=1)) != base


def test_gate_fails_without_digest(tmp_path, capsys):
    manifest = _write(tmp_path, digest="localhost/agent-integration-platform:dev")
    assert gate.main(["--manifest", str(manifest), "--eval", str(_eval_json(tmp_path, manifest))]) == 1
    assert "digest" in capsys.readouterr().err


def test_gate_fails_on_eval_hash_mismatch(tmp_path, capsys):
    manifest = _write(tmp_path)
    bad = _eval_json(tmp_path, manifest, cases_hash="0" * 64)
    assert gate.main(["--manifest", str(manifest), "--eval", str(bad)]) == 1
    assert "does not match" in capsys.readouterr().err
    good = _eval_json(tmp_path, manifest)
    assert gate.main(["--manifest", str(manifest), "--eval", str(good)]) == 0
    low = _eval_json(tmp_path, manifest, rate=0.5)
    assert gate.main(["--manifest", str(manifest), "--eval", str(low)]) == 1
