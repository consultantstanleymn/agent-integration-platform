"""The four tools. Each has a pydantic argument model (this is the input validation)."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from aip.config import Settings
from aip.integrations.webhooks import send_webhook, validate_target_url
from aip.models import Tenant
from aip.providers.base import Embedder, ToolSpec
from aip.rag.service import retrieve
from aip.tickets import create_ticket, get_ticket


@dataclass
class ToolContext:
    db: Session
    tenant: Tenant
    embedder: Embedder
    http: httpx.Client
    settings: Settings
    retrieved: dict[str, dict[str, Any]] = field(default_factory=dict)  # citation id -> hit
    run_key: str = field(default_factory=lambda: uuid.uuid4().hex)  # names this run in outbound event ids
    event_seq: int = 0  # counts outbound events in this run
    role: str = "agent"  # decides which access labels the search tool may read


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=3, ge=1, le=10)


class LookupArgs(BaseModel):
    ticket_id: int = Field(ge=1)


class CreateTicketArgs(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=5000)


class WebhookArgs(BaseModel):
    event: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_.]+$")
    payload: dict[str, Any] = Field(default_factory=dict)


def search_knowledge_base(ctx: ToolContext, a: SearchArgs) -> dict[str, Any]:
    hits = retrieve(ctx.db, ctx.tenant.id, ctx.embedder, a.query, a.k, ctx.role)
    out = []
    for h in hits:
        ctx.retrieved[f"{h.doc}#{h.chunk}"] = {
            "doc": h.doc,
            "chunk": h.chunk,
            "score": round(h.score, 4),
        }
        out.append({"doc": h.doc, "chunk": h.chunk, "score": round(h.score, 4), "text": h.text})
    return {"hits": out}


def lookup_ticket(ctx: ToolContext, a: LookupArgs) -> dict[str, Any]:
    t = get_ticket(ctx.db, ctx.tenant.id, a.ticket_id)
    if not t:
        return {"error": f"ticket {a.ticket_id} not found"}
    return {"id": t.id, "subject": t.subject, "status": t.status}


def create_ticket_tool(ctx: ToolContext, a: CreateTicketArgs) -> dict[str, Any]:
    t = create_ticket(ctx.db, ctx.tenant.id, a.subject, a.body)
    return {"id": t.id, "subject": t.subject, "status": t.status}


def call_customer_webhook(ctx: ToolContext, a: WebhookArgs) -> dict[str, Any]:
    url = ctx.tenant.webhook_url
    if not url:
        return {"error": "tenant has no webhook_url configured"}
    validate_target_url(url, ctx.settings.allow_private_webhook_targets)
    ctx.event_seq += 1
    event_id = f"{ctx.run_key}:{ctx.event_seq}"  # same id on every retry of this delivery
    res = send_webhook(
        url,
        ctx.tenant.webhook_secret,
        {"event": a.event, "data": a.payload},
        client=ctx.http,
        idempotency_key=event_id,
    )
    if not res.ok:
        return {"error": f"webhook delivery failed after {res.attempts} attempts (status {res.status_code})"}
    return {"event": a.event, "status_code": res.status_code, "attempts": res.attempts}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[ToolContext, Any], dict[str, Any]]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.args_model.model_json_schema(),
        )


REGISTRY: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "search_knowledge_base",
            "Search the customer's knowledge base. Returns text chunks with scores.",
            SearchArgs,
            search_knowledge_base,
        ),
        Tool(
            "lookup_ticket",
            "Get the status of a support ticket by numeric id.",
            LookupArgs,
            lookup_ticket,
        ),
        Tool("create_ticket", "Create a new support ticket.", CreateTicketArgs, create_ticket_tool),
        Tool(
            "call_customer_webhook",
            "Send a signed event to the customer's webhook.",
            WebhookArgs,
            call_customer_webhook,
        ),
    ]
}
