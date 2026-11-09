"""Team lead decisions on paused cases."""

import pytest
from sqlalchemy import select

from aip.db import make_session_factory
from aip.graph.service import CaseService
from aip.models import AuditLog

LARGE = "Please refund $150.00 for order ORD-5678."


@pytest.fixture
def hitl(env, tmp_path):
    svc = CaseService(
        checkpoint_url=f"sqlite:///{tmp_path / 'cp.sqlite'}",
        provider=env.app.state.chat,
        embedder=env.app.state.embedder,
        approval_limit_minor=10000,
    )
    acts = []
    original = svc._deps

    def deps(db):
        d = original(db)
        d.act = lambda state: acts.append(1) or {"done": True}
        return d

    svc._deps = deps
    env.app.state.case_service = svc
    env.acts = acts
    tenant = env.tenant()
    env.add_kb(tenant)
    key = env.client.post(
        f"/admin/tenants/{tenant['id']}/keys",
        json={"role": "team_lead", "label": "Lead One"},
        headers={"X-Admin-Token": "admin-secret"},
    ).json()["api_key"]
    env.lead = {"X-API-Key": key}
    env.t = tenant
    yield env
    svc.close()


def _start(env):
    r = env.client.post("/v1/cases", json={"subject": "Return", "body": LARGE}, headers=env.t["headers"])
    assert r.json()["status"] == "awaiting_approval"
    return r.json()["thread_id"]


def _decide(env, thread_id, headers, decision="approve"):
    return env.client.post(
        f"/v1/cases/{thread_id}/decision", json={"decision": decision, "reason": "ok"}, headers=headers
    )


def test_agent_role_cannot_decide_403(hitl):
    tid = _start(hitl)
    assert _decide(hitl, tid, hitl.t["headers"]).status_code == 403
    assert hitl.acts == []


def test_team_lead_approves_and_case_completes(hitl):
    tid = _start(hitl)
    r = _decide(hitl, tid, hitl.lead)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    assert hitl.acts == [1]


def test_team_lead_rejects_and_no_action(hitl):
    tid = _start(hitl)
    r = _decide(hitl, tid, hitl.lead, "reject")
    assert r.json()["status"] == "rejected"
    assert hitl.acts == []


def test_decide_twice_is_409(hitl):
    tid = _start(hitl)
    assert _decide(hitl, tid, hitl.lead).status_code == 200
    assert _decide(hitl, tid, hitl.lead).status_code == 409
    assert hitl.acts == [1]


def test_list_awaiting_approval(hitl):
    tid = _start(hitl)
    rows = hitl.client.get("/v1/cases?status=awaiting_approval", headers=hitl.t["headers"]).json()
    assert [r["thread_id"] for r in rows] == [tid]
    _decide(hitl, tid, hitl.lead)
    assert hitl.client.get("/v1/cases?status=awaiting_approval", headers=hitl.t["headers"]).json() == []


def test_decision_audit_rows(hitl):
    tid = _start(hitl)
    _decide(hitl, tid, hitl.lead)
    with make_session_factory(hitl.engine)() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.action == "case.approved"))
        assert row.detail["actor"] == "Lead One"
        assert row.detail["thread_id"] == tid
