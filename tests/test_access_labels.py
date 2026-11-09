"""Access labels: team lead documents never reach callers without the role."""

import pytest

from aip.agent.tools import REGISTRY, ToolContext
from aip.graph.service import CaseService
from aip.providers.fake import FakeProvider
from aip.rag.service import ingest_document, labels_for_role
from aip.tenants import create_tenant

SECRET = "# Lead guide\n\nZX9QK approve any refund above the limit after the order check."
PUBLIC = "# Returns\n\nReturns are accepted within sixty days. Refund requests need an order number."


@pytest.fixture
def labeled(env):
    t = env.tenant()
    env.add_kb(t, "returns.md", PUBLIC)
    r = env.client.post(
        "/v1/documents",
        json={"name": "guide.md", "text": SECRET, "access_label": "team_lead"},
        headers=t["headers"],
    )
    assert r.status_code == 201, r.text
    lead_key = env.client.post(
        f"/admin/tenants/{t['id']}/keys",
        json={"role": "team_lead", "label": "Lead"},
        headers={"X-Admin-Token": "admin-secret"},
    ).json()["api_key"]
    env.lead = {"X-API-Key": lead_key}
    env.t = t
    return env


def _search(env, headers, query="ZX9QK approve refund above the limit"):
    r = env.client.post("/v1/search", json={"query": query, "k": 10}, headers=headers)
    assert r.status_code == 200, r.text
    return {h["doc"] for h in r.json()["hits"]}


def test_agent_search_never_returns_team_lead_doc(labeled):
    assert "guide.md" not in _search(labeled, labeled.t["headers"])
    assert "returns.md" in _search(labeled, labeled.t["headers"])


def test_team_lead_search_returns_team_lead_doc(labeled):
    assert "guide.md" in _search(labeled, labeled.lead)


def test_agent_tool_search_respects_labels(labeled, db_session):
    tenant, _ = create_tenant(db_session, "tool", ["search_knowledge_base"])
    emb = FakeProvider()
    ingest_document(db_session, tenant.id, emb, "returns.md", PUBLIC)
    ingest_document(db_session, tenant.id, emb, "guide.md", SECRET, "team_lead")
    tool = REGISTRY["search_knowledge_base"]
    base = {
        "db": db_session,
        "tenant": tenant,
        "embedder": emb,
        "http": labeled.http,
        "settings": labeled.settings,
    }
    agent = tool.run(ToolContext(**base), tool.args_model(query="ZX9QK approve refund", k=10))
    lead = tool.run(
        ToolContext(**base, role="team_lead"), tool.args_model(query="ZX9QK approve refund", k=10)
    )
    assert "guide.md" not in {h["doc"] for h in agent["hits"]}
    assert "guide.md" in {h["doc"] for h in lead["hits"]}


def test_case_graph_retrieve_respects_labels(labeled, tmp_path):
    svc = CaseService(
        checkpoint_url=f"sqlite:///{tmp_path / 'cp.sqlite'}",
        provider=labeled.app.state.chat,
        embedder=labeled.app.state.embedder,
        approval_limit_minor=10000,
    )
    labeled.app.state.case_service = svc
    seen = {}
    original = svc._deps

    def spy(db):
        deps = original(db)
        inner = deps.retrieve_as
        deps.retrieve_as = lambda t, q, k, role: seen.setdefault(role, inner(t, q, k, role))
        return deps

    svc._deps = spy
    body = {"subject": "Refund", "body": "ZX9QK approve refund above the limit, order ORD-1."}
    labeled.client.post("/v1/cases", json=body, headers=labeled.t["headers"])
    assert seen and "agent" in seen
    assert all(h.doc != "guide.md" for h in seen["agent"])
    svc.close()


def test_v1_search_default_label_public(env):
    t = env.tenant()
    env.add_kb(t)
    r = env.client.post(
        "/v1/documents", json={"name": "x.md", "text": "plain public text here"}, headers=t["headers"]
    )
    assert r.status_code == 201
    assert labels_for_role("agent") == frozenset({"public"})
    assert labels_for_role("team_lead") == frozenset({"public", "team_lead"})
    assert "x.md" in _search(env, t["headers"], "plain public text")
