"""audit_log cannot be changed once written."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from aip.audit import audit
from aip.db import make_session_factory


def _row(env):
    factory = make_session_factory(env.engine)
    with factory() as db:
        audit(db, None, "test.event", a=1)
        db.commit()


def test_update_audit_row_fails(env):
    _row(env)
    with env.engine.begin() as conn, pytest.raises(DatabaseError, match="append-only"):
        conn.execute(text("UPDATE audit_log SET action = 'x'"))


def test_delete_audit_row_fails(env):
    _row(env)
    with env.engine.begin() as conn, pytest.raises(DatabaseError, match="append-only"):
        conn.execute(text("DELETE FROM audit_log"))


def test_migration_0003_installs_and_drops_triggers(tmp_path, monkeypatch):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    root = Path(__file__).resolve().parents[1]
    db = f"sqlite:///{tmp_path / 'm3.db'}"
    monkeypatch.setenv("DATABASE_URL", db)
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))

    def triggers() -> set[str]:
        with create_engine(db).connect() as conn:
            rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")).all()
        return {r[0] for r in rows}

    command.upgrade(cfg, "0003")
    assert {"audit_log_no_update", "audit_log_no_delete"} <= triggers()
    command.downgrade(cfg, "0002")
    assert triggers() == set()
    assert inspect(create_engine(db)).has_table("audit_log")
