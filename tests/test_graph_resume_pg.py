"""Resume the case graph from a Postgres checkpoint after a process restart."""

import uuid
from typing import Any

import pytest

from aip.graph.build import build_graph, postgres_checkpointer, resume_case, run_case
from aip.graph.nodes import NodeDeps
from aip.graph.state import CaseState, Ticket
from aip.providers.fake import FakeProvider

pytestmark = pytest.mark.pg


class CountingAct:
    def __init__(self) -> None:
        self.calls = 0
        self.states: list[CaseState] = []

    def __call__(self, state: CaseState) -> dict[str, Any]:
        self.calls += 1
        self.states.append(state)
        return {"ok": True}


def _deps(acting: CountingAct) -> NodeDeps:
    return NodeDeps(
        provider=FakeProvider(),
        retrieve=lambda tenant_id, query, k: [],
        approval_limit_minor=1000,
        act=acting,
        record=lambda state: None,
    )


def _state(thread_id: str) -> CaseState:
    return CaseState(
        tenant_id=1,
        thread_id=thread_id,
        ticket=Ticket(
            subject="Return request",
            body="Please refund $45.50 for order ORD-1234.",
        ),
    )


def test_resume_after_restart_postgres(pg_url: str) -> None:
    acting = CountingAct()
    deps = _deps(acting)
    state = _state(f"case-pg-{uuid.uuid4()}")

    with postgres_checkpointer(pg_url) as saver:
        graph = build_graph(deps, saver)
        paused = run_case(graph, state)
        assert paused.status == "awaiting_approval"
        assert paused.decision is None
        assert acting.calls == 0

    with postgres_checkpointer(pg_url) as saver2:
        graph2 = build_graph(deps, saver2)
        result = resume_case(
            graph2,
            state.thread_id,
            {"decision": "approve", "actor": "team-lead", "reason": "approved"},
        )

    assert result.status == "completed"
    assert result.decision is not None
    assert result.decision.decision == "approve"
    assert result.action_result == {"ok": True}
    assert acting.calls == 1
