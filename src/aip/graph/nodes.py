"""Pure node functions for the case workflow graph.

Each function takes the current state plus its dependencies and returns a
partial update. The graph merges the update into the state.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import ValidationError

from aip.graph.state import CaseState, Proposal
from aip.providers.base import ChatProvider, Message
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


def classify(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    messages = [
        Message(role="system", content=CLASSIFY_PROMPT),
        Message(role="user", content=f"{state.ticket.subject}\n\n{state.ticket.body}"),
    ]
    response = deps.provider.chat(messages, [])
    steps = state.steps_used + 1
    try:
        category = _json_object(response.content).get("category")
    except (ValueError, json.JSONDecodeError):
        category = None
    return {
        "category": category if isinstance(category, str) else None,
        "steps_used": steps,
    }


def retrieve(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    query = f"{state.ticket.subject}\n{state.ticket.body}"
    hits = deps.retrieve(state.tenant_id, query, RETRIEVAL_K)
    return {"retrieved": [asdict(hit) for hit in hits]}


def propose(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    sources = "\n\n".join(
        f"[doc:{item.get('doc', '')}#{item.get('chunk', '')}] {item.get('text', '')}" for item in state.retrieved
    )
    messages = [
        Message(role="system", content=PROPOSE_PROMPT),
        Message(
            role="user",
            content=(f"Ticket: {state.ticket.subject}\n{state.ticket.body}\n\nPolicy:\n{sources}"),
        ),
    ]
    response = deps.provider.chat(messages, [])
    steps = state.steps_used + 1
    try:
        data = _json_object(response.content)
        proposal = Proposal.model_validate(data)
    except (ValidationError, ValueError, json.JSONDecodeError):
        return {
            "status": "refused",
            "refusal_reason": "invalid_proposal",
            "steps_used": steps,
        }
    return {"proposal": proposal, "steps_used": steps}


def check_authority(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    if state.proposal is None:
        return {"needs_approval": False}
    return {"needs_approval": state.proposal.amount_minor > deps.approval_limit_minor}


def act(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    return {"action_result": deps.act(state)}


def record(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    deps.record(state)
    if state.status == "running":
        return {"status": "completed"}
    return {}
