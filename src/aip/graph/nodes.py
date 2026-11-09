"""Pure node functions for the case workflow graph.

Each function takes the current state plus its dependencies and returns a
partial update. The graph merges the update into the state.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import ValidationError

from aip.graph.authority import Authority, evaluate, fallback_authority
from aip.graph.state import CaseState, Proposal
from aip.observability import llm_span, record_usage
from aip.providers.base import ChatProvider, LLMResponse, Message
from aip.providers.pricing import PriceTable, estimate_cost_usd
from aip.rag.store import Hit

CLASSIFY_PROMPT = """CLASSIFY the support ticket into exactly one category.
Reply with JSON only: {"category": "<category>"}.
Allowed categories: refund, warranty, damage, account, other."""

PROPOSE_PROMPT = """PROPOSE a resolution for the support ticket.
Reply with JSON only: {"action": "refund|replacement|store_credit|escalate",
"amount_minor": <non-negative integer>, "order_id": <string or null>,
"reason": "<short reason>", "citations": ["<doc#chunk>"]}.
Use only the retrieved policy text below. If the request is outside policy,
use "escalate" with amount_minor 0."""

RETRIEVAL_K = 5


@dataclass
class NodeDeps:
    provider: ChatProvider
    retrieve: Callable[[int, str, int], list[Hit]]
    approval_limit_minor: int
    act: Callable[[CaseState], dict[str, Any]]
    record: Callable[[CaseState], None]
    max_steps: int = 6
    max_tokens: int = 4000
    authority: Authority | None = None
    prices: PriceTable = field(default_factory=dict)
    retrieve_as: Callable[[int, str, int, str], list[Hit]] | None = None  # role-aware retrieval


def _json_object(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`").strip()
        if body.lower().startswith("json"):
            body = body[4:].strip()
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in response")
    data = json.loads(body[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data


def over_budget(state: CaseState, deps: NodeDeps) -> bool:
    """Return True when the run has spent its step budget or its token budget."""
    return state.steps_used >= deps.max_steps or state.tokens_used >= deps.max_tokens


def charge(state: CaseState, response: LLMResponse, deps: NodeDeps | None = None) -> dict[str, Any]:
    """Return the step, token and usage counters after one LLM call."""
    usage = response.usage
    totals = dict(state.usage)
    totals["input_tokens"] = int(totals.get("input_tokens", 0)) + usage.input_tokens
    totals["output_tokens"] = int(totals.get("output_tokens", 0)) + usage.output_tokens
    if deps is not None:
        cost = estimate_cost_usd(getattr(deps.provider, "model", None), usage, deps.prices)
        if cost is not None:
            totals["cost_usd"] = round(float(totals.get("cost_usd") or 0.0) + cost, 8)
    return {
        "steps_used": state.steps_used + 1,
        "tokens_used": state.tokens_used + usage.input_tokens + usage.output_tokens,
        "usage": totals,
    }


def _chat(deps: NodeDeps, messages: list[Message]) -> LLMResponse:
    """One chat call inside a GenAI span."""
    with llm_span(getattr(deps.provider, "name", "unknown"), getattr(deps.provider, "model", None)) as sp:
        response = deps.provider.chat(messages, [])
        record_usage(sp, response.usage.input_tokens, response.usage.output_tokens)
    return response


def classify(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    if over_budget(state, deps):
        return {"status": "budget_exceeded"}
    messages = [
        Message(role="system", content=CLASSIFY_PROMPT),
        Message(role="user", content=f"{state.ticket.subject}\n\n{state.ticket.body}"),
    ]
    response = _chat(deps, messages)
    charged = charge(state, response, deps)
    try:
        category = _json_object(response.content).get("category")
    except (ValueError, json.JSONDecodeError):
        category = None
    return {
        "category": category if isinstance(category, str) else None,
        **charged,
    }


def retrieve(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    query = f"{state.ticket.subject}\n{state.ticket.body}"
    if deps.retrieve_as is not None:
        hits = deps.retrieve_as(state.tenant_id, query, RETRIEVAL_K, state.role)
    else:
        hits = deps.retrieve(state.tenant_id, query, RETRIEVAL_K)
    return {"retrieved": [asdict(hit) for hit in hits]}


def propose(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    if over_budget(state, deps):
        return {"status": "budget_exceeded"}
    sources = "\n\n".join(
        f"[doc:{item.get('doc', '')}#{item.get('chunk', '')}] {item.get('text', '')}"
        for item in state.retrieved
    )
    messages = [
        Message(role="system", content=PROPOSE_PROMPT),
        Message(
            role="user",
            content=(f"Ticket: {state.ticket.subject}\n{state.ticket.body}\n\nPolicy:\n{sources}"),
        ),
    ]
    response = _chat(deps, messages)
    charged = charge(state, response, deps)
    try:
        data = _json_object(response.content)
        proposal = Proposal.model_validate(data)
    except (ValidationError, ValueError, json.JSONDecodeError):
        return {
            "status": "refused",
            "refusal_reason": "invalid_proposal",
            **charged,
        }
    return {"proposal": proposal, **charged}


def check_authority(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    authority = deps.authority or fallback_authority(deps.approval_limit_minor)
    allowed, reason = evaluate(authority, state.ticket, state.proposal)
    if not allowed:
        return {"status": "refused", "refusal_reason": reason, "needs_approval": False}
    if state.proposal is None:
        return {"needs_approval": False}
    return {"needs_approval": state.proposal.amount_minor > authority.approval_limit_minor}


def act(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    return {"action_result": deps.act(state)}


def record(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    deps.record(state)
    if state.status == "running":
        return {"status": "completed"}
    return {}
