"""Step and token budgets stop a case run before any side effect."""

import pytest
from sqlalchemy import select

from aip.db import make_session_factory
from aip.graph.service import CaseService
from aip.models import AuditLog, Case

BODY = "Please refund $45.50 for order ORD-1234."


@pytest.fixture
def budget_env(env, tmp_path):
    services = []

    def make(**limits):
        svc = CaseService(
            checkpoint_url=f"sqlite:///{tmp_path / 'cp.sqlite'}",
            provider=env.app.state.chat,
            embedder=env.app.state.embedder,
            approval_limit_minor=env.settings.approval_limit_minor,
            **limits,
        )
        env.app.state.case_service = svc
        services.append(svc)
        return svc

    env.make_service = make
    yield env
    for svc in services:
        svc.close()


def _post(env, tenant):
    return env.client.post("/v1/cases", json={"subject": "Return", "body": BODY}, headers=tenant["headers"])


def _act_counter(svc):
    calls = []
    original = svc._deps

    def deps(db):
        d = original(db)
        d.act = lambda state: calls.append(1) or {"stub": True}
        return d

    svc._deps = deps
    return calls


def test_step_budget_stops_before_act(budget_env):
    svc = budget_env.make_service(max_steps=1)
    calls = _act_counter(svc)
    t = budget_env.tenant()
    budget_env.add_kb(t)
    body = _post(budget_env, t).json()
    assert body["status"] == "budget_exceeded"
    assert calls == []


def test_token_budget_stops_before_act(budget_env):
    svc = budget_env.make_service(max_tokens=10)
    calls = _act_counter(svc)
    t = budget_env.tenant()
    budget_env.add_kb(t)
    body = _post(budget_env, t).json()
    assert body["status"] == "budget_exceeded"
    assert calls == []


def test_budget_exceeded_has_no_side_effect(budget_env):
    svc = budget_env.make_service(max_steps=1)
    calls = _act_counter(svc)
    t = budget_env.tenant()
    budget_env.add_kb(t)
    _post(budget_env, t)
    assert calls == []
    with make_session_factory(budget_env.engine)() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.action == "case.recorded")).all()
        assert len(rows) == 1
        assert rows[0].detail["status"] == "budget_exceeded"


def test_tokens_used_recorded_on_case_row(budget_env):
    budget_env.make_service()
    t = budget_env.tenant()
    budget_env.add_kb(t)
    _post(budget_env, t)
    with make_session_factory(budget_env.engine)() as db:
        row = db.scalar(select(Case))
        assert row.tokens_used > 0
        assert row.steps_used == 2
