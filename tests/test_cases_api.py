"""Start and read cases over HTTP, plus the case CLI."""

import json

import pytest

from aip.cli import main
from aip.db import make_engine, make_session_factory
from aip.graph.service import CaseService
from aip.models import Base
from aip.tenants import create_tenant

SMALL_REFUND = "Please refund $45.50 for order ORD-1234."
LARGE_REFUND = "Please refund $150.00 for order ORD-5678."


@pytest.fixture
def case_env(env, tmp_path):
    env.app.state.case_service = CaseService(
        checkpoint_url=f"sqlite:///{tmp_path / 'checkpoints.sqlite'}",
        provider=env.app.state.chat,
        embedder=env.app.state.embedder,
        approval_limit_minor=env.settings.approval_limit_minor,
    )
    yield env
    env.app.state.case_service.close()


def _post(env, tenant, subject="Return request", body=SMALL_REFUND, order_id=None):
    payload = {"subject": subject, "body": body}
    if order_id is not None:
        payload["order_id"] = order_id
    return env.client.post("/v1/cases", json=payload, headers=tenant["headers"])


def test_post_case_small_refund_completes(case_env):
    t = case_env.tenant()
    case_env.add_kb(t)

    response = _post(case_env, t, order_id="ORD-1234")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["needs_approval"] is False
    assert body["proposal"]["action"] == "refund"
    assert body["proposal"]["amount_minor"] == 4550
    assert body["thread_id"]


def test_post_case_large_refund_awaits_approval(case_env):
    t = case_env.tenant()
    case_env.add_kb(t)

    response = _post(case_env, t, body=LARGE_REFUND)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "awaiting_approval"
    assert body["needs_approval"] is True
    assert body["proposal"]["amount_minor"] == 15000


def test_get_case_other_tenant_404(case_env):
    owner = case_env.tenant("owner")
    intruder = case_env.tenant("intruder")
    thread_id = _post(case_env, owner).json()["thread_id"]

    own = case_env.client.get(f"/v1/cases/{thread_id}", headers=owner["headers"])
    assert own.status_code == 200
    assert own.json()["thread_id"] == thread_id

    other = case_env.client.get(f"/v1/cases/{thread_id}", headers=intruder["headers"])
    assert other.status_code == 404


def test_case_writes_audit_row(case_env):
    t = case_env.tenant()

    _post(case_env, t)

    actions = {row["action"] for row in case_env.client.get("/v1/audit", headers=t["headers"]).json()}
    assert "case.recorded" in actions


def test_cli_case_start(tmp_path, monkeypatch, capsys):
    db_url = f"sqlite:///{tmp_path / 'cli.db'}"
    checkpoint_url = f"sqlite:///{tmp_path / 'cli-checkpoints.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("CHECKPOINT_URL", checkpoint_url)

    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        tenant, _ = create_tenant(db, "cli-co", ["search_knowledge_base"], None)
        db.commit()
        tenant_id = tenant.id

    code = main(
        [
            "case",
            "start",
            "--tenant-id",
            str(tenant_id),
            "--subject",
            "Return request",
            "--body",
            SMALL_REFUND,
        ]
    )

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "completed"
    assert out["proposal"]["amount_minor"] == 4550
    assert out["thread_id"]
    engine.dispose()
