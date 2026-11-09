"""The act node writes through the one MCP tool, once."""

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_mcp_server import ROOT

from aip.api.app import create_app
from aip.config import Settings
from aip.db import make_engine, make_session_factory
from aip.graph.actions import InProcessActionPort, McpActionPort
from aip.graph.state import Proposal
from aip.models import Case, ReturnAuthorization
from aip.providers.fake import FakeProvider
from aip.tenants import create_tenant

LARGE = "Please refund $150.00 for order ORD-5678."


def _make_app(tmp_path, monkeypatch, port):
    url = f"sqlite:///{tmp_path / 'act.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    settings = Settings(
        admin_token="admin-secret",
        database_url=url,  # type: ignore[arg-type]
        checkpoint_url=f"sqlite:///{tmp_path / 'cp.sqlite'}",
        rate_limit_per_minute=1000,
        action_port=port,
    )
    engine = make_engine(url)
    app = create_app(settings, engine=engine, chat=FakeProvider(), embedder=FakeProvider())
    factory = make_session_factory(engine)
    with factory() as db:
        tenant, key = create_tenant(db, "act-co", ["search_knowledge_base"], None)
        db.commit()
        tid = tenant.id
    lead = (
        TestClient(app)
        .post(
            f"/admin/tenants/{tid}/keys",
            json={"role": "team_lead", "label": "Lead"},
            headers={"X-Admin-Token": "admin-secret"},
        )
        .json()["api_key"]
    )
    return app, TestClient(app), factory, {"X-API-Key": key}, {"X-API-Key": lead}


def _case_to_approval(client, agent):
    r = client.post("/v1/cases", json={"subject": "Return", "body": LARGE}, headers=agent)
    assert r.json()["status"] == "awaiting_approval"
    return r.json()["thread_id"]


def test_approved_case_creates_one_ra_via_mcp(tmp_path, monkeypatch):
    app, client, factory, agent, lead = _make_app(tmp_path, monkeypatch, "mcp")
    tid = _case_to_approval(client, agent)
    r = client.post(f"/v1/cases/{tid}/decision", json={"decision": "approve"}, headers=lead)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    with factory() as db:
        rows = db.scalars(select(ReturnAuthorization)).all()
        assert len(rows) == 1 and rows[0].amount_minor == 15000
    app.state.case_service.close()


def test_resume_twice_does_not_create_second_ra(tmp_path, monkeypatch):
    app, client, factory, agent, lead = _make_app(tmp_path, monkeypatch, "inprocess")
    tid = _case_to_approval(client, agent)
    assert (
        client.post(f"/v1/cases/{tid}/decision", json={"decision": "approve"}, headers=lead).status_code
        == 200
    )
    assert (
        client.post(f"/v1/cases/{tid}/decision", json={"decision": "approve"}, headers=lead).status_code
        == 409
    )
    # Replay the act through the port with the same key: still one row.
    port = InProcessActionPort(factory, 50000)
    proposal = Proposal(action="refund", amount_minor=15000, order_id="ORD-5678", reason="r", citations=[])
    with factory() as db:
        tenant_id = db.scalar(select(Case)).tenant_id
    again = port.create_return_authorization(tenant_id, proposal, f"{tid}:act")
    assert again["replayed"] is True
    with factory() as db:
        assert len(db.scalars(select(ReturnAuthorization)).all()) == 1
    app.state.case_service.close()


def test_inprocess_port_matches_mcp_result_shape(tmp_path, monkeypatch):
    app, _client, factory, _agent, _lead = _make_app(tmp_path, monkeypatch, "inprocess")
    with factory() as db:
        tenant, _key = create_tenant(db, "shape-co", ["search_knowledge_base"], None)
        db.commit()
        tenant_id = tenant.id
    proposal = Proposal(action="refund", amount_minor=100, order_id="ORD-1", reason="r", citations=[])
    local = InProcessActionPort(factory, 50000).create_return_authorization(tenant_id, proposal, "shape:1")
    remote = McpActionPort(f"sqlite:///{tmp_path / 'act.db'}", 50000).create_return_authorization(
        tenant_id, proposal, "shape:2"
    )
    assert set(local) == set(remote)
    app.state.case_service.close()


def test_case_row_stores_ra_number(tmp_path, monkeypatch):
    app, client, factory, agent, lead = _make_app(tmp_path, monkeypatch, "inprocess")
    tid = _case_to_approval(client, agent)
    client.post(f"/v1/cases/{tid}/decision", json={"decision": "approve"}, headers=lead)
    row = client.get(f"/v1/cases/{tid}", headers=agent).json()
    assert row["action_result"]["ra_number"].startswith("RA-")
    app.state.case_service.close()
