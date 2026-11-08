import pytest

from aip.agent.loop import AgentQuestion, run_agent
from aip.agent.tools import ToolContext
from aip.providers.base import LLMResponse, ProviderError, ToolCall
from aip.providers.fake import FakeProvider
from aip.rag.service import ingest_document
from aip.tenants import ALL_TOOLS, create_tenant


class Scripted:
    """A test provider that returns pre-set responses."""

    name = "scripted"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        r = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def make_ctx(db, env, tools=None):
    t, _ = create_tenant(db, "t", tools if tools is not None else list(ALL_TOOLS))
    ingest_document(db, t.id, FakeProvider(), "kb.md", "# Refunds\n\nFull refund within 30 days.")
    return ToolContext(db=db, tenant=t, embedder=FakeProvider(), http=env.http, settings=env.settings)


def go(provider, ctx, q="refund?", max_steps=5, only=None):
    return run_agent(provider, ctx, q, "sys", "v-test", max_steps=max_steps, only_tools=only)


def test_agent_answers_with_citation_from_retrieved_chunk(db_session, env):
    r = go(FakeProvider(), make_ctx(db_session, env), "How long to get a full refund?")
    assert r.status == "ok"
    assert "30 days" in r.answer
    assert [(c.doc, c.chunk) for c in r.citations] == [("kb.md", 0)]
    assert r.steps[0]["tool"] == "search_knowledge_base"


def test_max_steps_guardrail_stops_looping_model(db_session, env):
    loop = Scripted(
        LLMResponse(tool_calls=[ToolCall(id="1", name="search_knowledge_base", arguments={"query": "x"})])
    )
    r = go(loop, make_ctx(db_session, env), max_steps=3)
    assert r.status == "max_steps"
    assert loop.calls == 3


def test_tool_not_in_allow_list_is_denied(db_session, env):
    ctx = make_ctx(db_session, env, tools=["search_knowledge_base"])
    prov = Scripted(
        LLMResponse(tool_calls=[ToolCall(id="1", name="create_ticket", arguments={"subject": "s"})]),
        LLMResponse(content="done"),
    )
    r = go(prov, ctx)
    assert r.steps[0]["outcome"] == "denied"
    from aip.models import Ticket

    assert db_session.query(Ticket).count() == 0


def test_invented_tool_name_is_denied(db_session, env):
    prov = Scripted(
        LLMResponse(tool_calls=[ToolCall(id="1", name="drop_database", arguments={})]),
        LLMResponse(content="ok"),
    )
    r = go(prov, make_ctx(db_session, env))
    assert r.steps[0]["outcome"] == "denied"


def test_fake_provider_refuses_tool_it_was_not_offered(db_session, env):
    ctx = make_ctx(db_session, env, tools=["search_knowledge_base"])
    r = go(FakeProvider(), ctx, "please create a ticket about my printer")
    assert "not allowed" in r.answer


def test_invalid_tool_arguments_reported_back_to_model(db_session, env):
    prov = Scripted(
        LLMResponse(tool_calls=[ToolCall(id="1", name="lookup_ticket", arguments={"ticket_id": "abc"})]),
        LLMResponse(content="sorry"),
    )
    r = go(prov, make_ctx(db_session, env))
    assert r.steps[0]["outcome"] == "invalid_args"
    assert r.status == "ok"


def test_answer_citing_unretrieved_source_is_withheld(db_session, env):
    prov = Scripted(LLMResponse(content="Refunds take 90 days [made-up.md#3]"))
    r = go(prov, make_ctx(db_session, env))
    assert r.status == "invalid_output"
    assert "90 days" not in r.answer


def test_empty_model_answer_is_invalid(db_session, env):
    r = go(Scripted(LLMResponse(content="")), make_ctx(db_session, env))
    assert r.status == "invalid_output"


def test_provider_error_is_handled(db_session, env):
    r = go(Scripted(ProviderError("down")), make_ctx(db_session, env))
    assert r.status == "provider_error"


def test_input_validation():
    with pytest.raises(ValueError):
        AgentQuestion(question="   ")
    with pytest.raises(ValueError):
        AgentQuestion(question="x" * 2001)


def test_tool_exception_does_not_crash_run(db_session, env):
    from aip.agent import tools

    def boom(ctx, a):
        raise RuntimeError("db exploded")

    ctx = make_ctx(db_session, env)
    object.__setattr__(tools.REGISTRY["lookup_ticket"], "run", boom)
    try:
        r = go(FakeProvider(), ctx, "status of T-1")
    finally:
        object.__setattr__(tools.REGISTRY["lookup_ticket"], "run", tools.lookup_ticket)
    assert r.steps[0]["outcome"] == "error"
    assert "failed" in r.answer


def test_pii_redacted_in_stored_run_and_audit(env):
    t = env.tenant()
    env.add_kb(t)
    env.client.post(
        "/v1/agent/run",
        json={"question": "my email is jane@example.com refund policy"},
        headers=t["headers"],
    )
    from sqlalchemy.orm import Session

    from aip.models import AgentRun

    with Session(env.engine) as s:
        assert "jane@example.com" not in s.query(AgentRun).one().question


def test_agent_endpoint_end_to_end(env):
    t = env.tenant()
    env.add_kb(t)
    r = env.client.post(
        "/v1/agent/run", json={"question": "How long for a full refund?"}, headers=t["headers"]
    )
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert "30 days" in body["answer"] and body["citations"][0]["doc"] == "kb.md"
    assert env.client.post("/v1/agent/run", json={"question": ""}, headers=t["headers"]).status_code == 422


def test_create_ticket_tool_via_agent(env):
    t = env.tenant(tools=["create_ticket", "lookup_ticket"])
    r = env.client.post(
        "/v1/agent/run",
        json={"question": "create a ticket about broken printer"},
        headers=t["headers"],
    ).json()
    assert "created ticket" in r["answer"]
    tickets = env.client.get("/v1/tickets", headers=t["headers"]).json()
    assert tickets[0]["subject"] == "broken printer"


def test_call_customer_webhook_tool_sends_signed_request(env):
    t = env.tenant(tools=["call_customer_webhook"], webhook_url="http://crm.example.test/hooks/receive")
    r = env.client.post(
        "/v1/agent/run", json={"question": "notify the customer webhook"}, headers=t["headers"]
    ).json()
    assert "customer webhook" in r["answer"], r
    req = env.requests[-1]
    assert str(req.url) == "http://crm.example.test/hooks/receive"
    from aip.integrations.webhooks import verify

    assert verify(t["webhook_secret"], req.headers["x-timestamp"], req.headers["x-signature"], req.content)
