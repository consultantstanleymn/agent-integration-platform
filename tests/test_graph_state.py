"""Tests for the typed case state and the pure graph nodes."""

import pytest
from pydantic import ValidationError

from aip.graph.nodes import NodeDeps, check_authority, classify, propose
from aip.graph.state import CaseState, Proposal, Ticket
from aip.providers.base import ChatProvider, LLMResponse, Message, ToolSpec


class StubProvider:
    name = "stub"

    def __init__(self, content: str = "") -> None:
        self.content = content
        self.calls: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.content)


def _deps(provider: ChatProvider, limit: int = 1000) -> NodeDeps:
    return NodeDeps(
        provider=provider,
        retrieve=lambda tenant_id, query, k: [],
        approval_limit_minor=limit,
        act=lambda state: {"ok": True},
        record=lambda state: None,
    )


def _state(**overrides: object) -> CaseState:
    data = {
        "tenant_id": 1,
        "thread_id": "case-1",
        "ticket": Ticket(subject="Return boots", body="The boots do not fit."),
    }
    data.update(overrides)
    return CaseState.model_validate(data)


def test_case_state_defaults() -> None:
    state = _state()

    assert state.role == "agent"
    assert state.category is None
    assert state.retrieved == []
    assert state.proposal is None
    assert state.needs_approval is False
    assert state.decision is None
    assert state.status == "running"
    assert state.refusal_reason is None
    assert state.steps_used == 0
    assert state.tokens_used == 0
    assert state.action_result is None


def test_proposal_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        Proposal(action="refund", amount_minor=-1, reason="bad")


def test_propose_invalid_json_refuses() -> None:
    provider = StubProvider(content="I cannot answer that.")
    state = _state(steps_used=2)

    update = propose(state, _deps(provider))

    assert update["status"] == "refused"
    assert update["refusal_reason"] == "invalid_proposal"
    assert update["steps_used"] == 3


def test_check_authority_sets_needs_approval_over_limit() -> None:
    state = _state(
        proposal=Proposal(action="refund", amount_minor=5000, order_id="ORD-1", reason="full refund"),
    )

    update = check_authority(state, _deps(StubProvider(), limit=1000))

    assert update["needs_approval"] is True


def test_check_authority_no_approval_under_limit() -> None:
    state = _state(
        proposal=Proposal(action="store_credit", amount_minor=500, reason="goodwill"),
    )

    update = check_authority(state, _deps(StubProvider(), limit=1000))

    assert update["needs_approval"] is False


def test_classify_parses_category() -> None:
    provider = StubProvider(content='{"category": "returns"}')
    state = _state()

    update = classify(state, _deps(provider))

    assert update["category"] == "returns"
    assert update["steps_used"] == 1
    assert provider.calls[0][0].content.startswith("CLASSIFY")
