"""The security exceptions file and its checker."""

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_exc", ROOT / "scripts" / "check_security_exceptions.py")
assert spec and spec.loader
check_exc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_exc)


def _item(**over):
    base = {
        "id": "X-1",
        "tool": "pip-audit",
        "reason": "no fix yet",
        "owner": "Stanley Sujith Nelavala",
        "expires": (date.today() + timedelta(days=30)).isoformat(),
        "review_note": "check weekly",
    }
    return {**base, **over}


def test_exceptions_file_valid():
    assert check_exc.check(ROOT / "security" / "exceptions.json") == []
    assert check_exc.main(["x", str(ROOT / "security" / "exceptions.json")]) == 0


def test_expired_exception_fails(tmp_path):
    path = tmp_path / "e.json"
    path.write_text(json.dumps([_item(expires="2020-01-01")]))
    assert any("expired" in p for p in check_exc.check(path))
    assert check_exc.main(["x", str(path)]) == 1


def test_missing_field_fails(tmp_path):
    item = _item()
    del item["owner"]
    path = tmp_path / "e.json"
    path.write_text(json.dumps([item]))
    assert any("missing field owner" in p for p in check_exc.check(path))
    far = tmp_path / "far.json"
    far.write_text(json.dumps([_item(expires=(date.today() + timedelta(days=200)).isoformat())]))
    assert any("90 days" in p for p in check_exc.check(far))
