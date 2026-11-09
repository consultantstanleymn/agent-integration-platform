"""Typed state for the case workflow graph."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Ticket(BaseModel):
    id: int | None = None
    subject: str
    body: str
    order_id: str | None = None
    amount_minor: int | None = None


class Proposal(BaseModel):
    action: Literal["refund", "replacement", "store_credit", "escalate"]
    amount_minor: int = Field(default=0, ge=0)
    order_id: str | None = None
    reason: str = Field(max_length=500)
    citations: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    decision: Literal["approve", "reject", "expired"]
    actor: str
    reason: str = ""


class CaseState(BaseModel):
    tenant_id: int
    thread_id: str
    role: Literal["agent", "team_lead"] = "agent"
    ticket: Ticket
    category: str | None = None
    retrieved: list[dict[str, Any]] = Field(default_factory=list)
    proposal: Proposal | None = None
    needs_approval: bool = False
    decision: Decision | None = None
    status: Literal[
        "running",
        "awaiting_approval",
        "completed",
        "refused",
        "rejected",
        "budget_exceeded",
        "provider_error",
    ] = "running"
    refusal_reason: str | None = None
    steps_used: int = 0
    tokens_used: int = 0
    action_result: dict[str, Any] | None = None
