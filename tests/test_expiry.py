"""Pending approvals expire to rejected."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from aip.cli import main
from aip.db import make_engine, make_session_factory
from aip.graph.service import CaseService
from aip.models import AuditLog, Base, Case, Tenant
from aip.tenants import create_tenant

LARGE = "Please refund $150.00 for order ORD-5678."


@pytest.fixture
def svc_env(env, tmp_path):
    svc = CaseService(
        checkpoint_url=f"sqlite:///{tmp_path / 'cp.sqlite'}",
        provider=env.app.state.chat,
        embedder=env.app.state.embedder,
        approval_limit_minor=10000,
    )
    env.app.state.case_service = svc
    t = env.tenant()
    env.add_kb(t)
    env.client.post("/v1/cases", json={"subject": "Return", "body": LARGE}, headers=t["headers"])
    env.svc = svc
    yield env
    svc.close()


def test_expire_pending_rejects_old_cases(svc_env):
    later = datetime.now(UTC) + timedelta(hours=30)
    with make_session_factory(svc_env.engine)() as db:
        assert svc_env.svc.expire_pending(db, later, timedelta(hours=24)) == 1
        assert db.scalar(select(Case)).status == "rejected"
        assert db.scalar(select(AuditLog).where(AuditLog.action == "case.expired")) is not None


def test_expire_keeps_recent_cases(svc_env):
    now = datetime.now(UTC)
    with make_session_factory(svc_env.engine)() as db:
        assert svc_env.svc.expire_pending(db, now, timedelta(hours=24)) == 0
        assert db.scalar(select(Case)).status == "awaiting_approval"


def test_cli_cases_expire(tmp_path, monkeypatch, capsys):
    db_url = f"sqlite:///{tmp_path / 'c.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("CHECKPOINT_URL", f"sqlite:///{tmp_path / 'cp.sqlite'}")
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        tenant, _ = create_tenant(db, "x", ["search_knowledge_base"], None)
        db.commit()
        tid = tenant.id
    assert main(["case", "start", "--tenant-id", str(tid), "--subject", "Return", "--body", LARGE]) == 0
    capsys.readouterr()
    assert main(["cases", "expire", "--older-than-hours", "0"]) == 0
    assert '"expired": 1' in capsys.readouterr().out
    with factory() as db:
        assert db.scalar(select(Case)).status == "rejected"
        assert db.get(Tenant, tid) is not None
