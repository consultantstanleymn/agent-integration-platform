"""OpenTelemetry spans: HTTP server span, parent from traceparent, GenAI attributes, graph nodes."""

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aip.graph.service import CaseService
from aip.observability import setup_tracing

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
PARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
TICKET_TEXT = "Please refund $150.00 for order ORD-4242, my email is ticket.person@example.com"


@pytest.fixture
def traced(env, tmp_path):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    setup_tracing("test", provider=provider)  # after the app exists, so it is not replaced
    svc = CaseService(
        checkpoint_url=f"sqlite:///{tmp_path / 'cp.sqlite'}",
        provider=env.app.state.chat,
        embedder=env.app.state.embedder,
        approval_limit_minor=10000,
    )
    env.app.state.case_service = svc
    env.exporter = exporter
    env.t = env.tenant()
    env.add_kb(env.t)
    exporter.clear()
    yield env
    svc.close()
    setup_tracing("test")  # back to a provider with no exporter


def _case(env, **headers):
    body = {"subject": "Refund", "body": TICKET_TEXT}
    return env.client.post("/v1/cases", json=body, headers={**env.t["headers"], **headers})


def test_request_creates_server_span(traced):
    assert traced.client.get("/health").status_code == 200
    spans = traced.exporter.get_finished_spans()
    server = [s for s in spans if s.kind.name == "SERVER"]
    assert server and server[0].name == "GET /health"
    assert server[0].attributes["http.response.status_code"] == 200


def test_traceparent_is_parent(traced):
    r = _case(traced, traceparent=PARENT)
    assert r.status_code == 201
    spans = traced.exporter.get_finished_spans()
    server = next(s for s in spans if s.kind.name == "SERVER")
    assert format(server.context.trace_id, "032x") == TRACE_ID
    assert format(server.parent.span_id, "016x") == "b7ad6b7169203331"
    assert r.headers["X-Trace-Id"] == TRACE_ID
    assert {format(s.context.trace_id, "032x") for s in spans} == {TRACE_ID}


def test_llm_span_has_genai_attributes(traced):
    _case(traced)
    llm = [s for s in traced.exporter.get_finished_spans() if s.name.startswith("chat ")]
    assert len(llm) == 2  # classify and propose
    attrs = llm[0].attributes
    assert attrs["gen_ai.system"] == "fake"
    assert attrs["gen_ai.request.model"]
    assert attrs["gen_ai.usage.input_tokens"] > 0 and attrs["gen_ai.usage.output_tokens"] > 0


def test_graph_node_spans_in_order(traced):
    _case(traced)
    spans = sorted(traced.exporter.get_finished_spans(), key=lambda s: s.start_time)
    nodes = [s.name for s in spans if s.name.startswith("graph.node.")]
    assert nodes[:4] == [
        "graph.node.classify",
        "graph.node.retrieve",
        "graph.node.propose",
        "graph.node.check_authority",
    ]
    assert "graph.node.human_approval" in nodes


def test_mcp_span_on_approved_case(traced):
    thread = _case(traced).json()["thread_id"]
    lead = traced.client.post(
        f"/admin/tenants/{traced.t['id']}/keys",
        json={"role": "team_lead", "label": "Lead"},
        headers={"X-Admin-Token": "admin-secret"},
    ).json()["api_key"]
    traced.client.post(
        f"/v1/cases/{thread}/decision", json={"decision": "approve"}, headers={"X-API-Key": lead}
    )
    names = [s.name for s in traced.exporter.get_finished_spans()]
    assert "mcp.tool.create_return_authorization" in names


def test_no_ticket_text_in_span_attributes(traced):
    _case(traced)
    for s in traced.exporter.get_finished_spans():
        for value in dict(s.attributes or {}).values():
            text = str(value)
            assert (
                "ticket.person@example.com" not in text
                and "ORD-4242" not in text
                and "refund $150" not in text
            )
