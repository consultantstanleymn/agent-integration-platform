"""Tests for the case workflow graph build and its control flow."""

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from aip.graph.build import NODE_NAMES, build_graph, resume_case, run_case
from aip.graph.nodes import NodeDeps
from aip.graph.state import CaseState, Ticket
from aip.providers.fake import FakeProvider

LARGE_REFUND = "Please refund $45.50 for order ORD-1234."


class CountingAct:
    def __init__(self) -> None:
        self.calls = 0
        self.states: list[CaseState] = []

    def __call__(self, state: CaseState) -> dict[str, Any]:
        self.calls += 1
        self.states.append(state)
        return {"ok": True}


def _deps(limit: int = 1000) -> tuple[NodeDeps, CountingAct]:
    acting = CountingAct()
    deps = NodeDeps(
        provider=FakeProvider(),
        retrieve=lambda tenant_id, query, k: [],
        approval_limit_minor=limit,
        act=acting,
        record=lambda state: None,
    )
    return deps, acting


def _state(body: str, thread_id: str = "case-1") -> CaseState:
    return CaseState(
        tenant_id=1,
        thread_id=thread_id,
        ticket=Ticket(subject="Return request", body=body),
    )


def test_graph_node_names() -> None:
    deps, _ = _deps()

    graph = build_graph(deps, InMemorySaver())

    assert set(NODE_NAMES) <= set(graph.get_graph().nodes)


def test_small_refund_completes_without_approval() -> None:
    deps, acting = _deps(limit=10000)
    graph = build_graph(deps, InMemorySaver())

    result = run_case(graph, _state(LARGE_REFUND))

    assert result.status == "completed"
    assert result.proposal is not None
    assert result.proposal.amount_minor == 4550
    assert result.decision is None
    assert result.action_result == {"ok": True}
    assert acting.calls == 1


def test_large_refund_pauses_for_approval() -> None:
    deps, acting = _deps(limit=1000)
    graph = build_graph(deps, InMemorySaver())

    result = run_case(graph, _state(LARGE_REFUND))

    assert result.status == "awaiting_approval"
    assert result.proposal is not None
    assert result.proposal.amount_minor == 4550
    assert result.decision is None
    assert acting.calls == 0


def test_reject_ends_with_rejected_and_no_action() -> None:
    deps, acting = _deps(limit=1000)
    graph = build_graph(deps, InMemorySaver())
    run_case(graph, _state(LARGE_REFUND))

    result = resume_case(
        graph,
        "case-1",
        {"decision": "reject", "actor": "team-lead", "reason": "over limit"},
    )

    assert result.status == "rejected"
    assert result.decision is not None
    assert result.decision.decision == "reject"
    assert result.action_result is None
    assert acting.calls == 0
