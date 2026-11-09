import hashlib
import json
import uuid
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aip.agent.loop import AgentResult, run_agent
from aip.agent.prompts import load_prompt
from aip.agent.tools import ToolContext
from aip.api.deps import current_tenant, get_db, get_settings, require_admin
from aip.audit import audit
from aip.config import Settings
from aip.graph.service import CaseService, case_json
from aip.graph.state import Ticket as CaseTicket
from aip.integrations.crm import CrmClient, customer_to_text
from aip.integrations.idempotency import run_once
from aip.integrations.oauth import ClientCredentialsTokenProvider
from aip.integrations.webhooks import UnsafeTargetError, validate_target_url, verify
from aip.models import AuditLog, Chunk, Document, Tenant, TenantKey, Ticket
from aip.rag.extract import extract_text
from aip.rag.retriever import SearchQuery
from aip.rag.service import build_retriever, ingest_document, labels_for_role, retrieve
from aip.security import hash_key, new_api_key
from aip.tenants import ALL_TOOLS, create_tenant
from aip.tickets import create_ticket, get_ticket
from aip.worker import job_key

router = APIRouter()
MAX_UPLOAD = 2_000_000


# ---- schemas ----------------------------------------------------------------
class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    allowed_tools: list[
        Literal["search_knowledge_base", "lookup_ticket", "create_ticket", "call_customer_webhook"]
    ] = ["search_knowledge_base", "lookup_ticket"]
    webhook_url: str | None = None


class TenantKeyCreate(BaseModel):
    role: Literal["agent", "team_lead"] = "agent"
    label: str = Field(default="", max_length=100)


class DocumentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=500_000)
    access_label: Literal["public", "team_lead"] = "public"


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=3, ge=1, le=10)


class SearchV2In(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=5, ge=1, le=20)
    mode: Literal["vector", "keyword", "hybrid"] = "hybrid"
    doc_names: list[str] | None = Field(default=None, max_length=50)
    labels: list[Literal["public", "team_lead"]] | None = None


class AgentIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class TicketIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=5000)


class CaseIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=5000)
    order_id: str | None = Field(default=None, max_length=100)


class InboundEvent(BaseModel):
    event: Literal["ticket.created"]
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=5000)


def _ticket_json(t: Ticket) -> dict[str, Any]:
    return {
        "id": t.id,
        "subject": t.subject,
        "body": t.body,
        "status": t.status,
        "triage_note": t.triage_note,
    }


def _ctx(request: Request, db: Session, tenant: Tenant) -> ToolContext:
    st = request.app.state
    role = getattr(request.state, "role", "agent")
    return ToolContext(
        db=db, tenant=tenant, embedder=st.embedder, http=st.http, settings=st.settings, role=role
    )


def _agent(
    request: Request, db: Session, tenant: Tenant, question: str, only_tools: set[str] | None = None
) -> AgentResult:
    st = request.app.state
    s: Settings = st.settings
    prompt = load_prompt(s.prompts_dir, s.prompt_version)
    return run_agent(
        st.chat,
        _ctx(request, db, tenant),
        question,
        prompt,
        s.prompt_version,
        max_steps=s.max_agent_steps,
        only_tools=only_tools,
    )


# ---- public -----------------------------------------------------------------
@router.get("/health", tags=["ops"])
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(select(1))
    return {"status": "ok"}


# ---- admin ------------------------------------------------------------------
@router.post("/admin/tenants", status_code=201, tags=["admin"], dependencies=[Depends(require_admin)])
def admin_create_tenant(
    body: TenantCreate, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    if body.webhook_url:
        try:
            validate_target_url(body.webhook_url, request.app.state.settings.allow_private_webhook_targets)
        except UnsafeTargetError as exc:
            raise HTTPException(422, str(exc)) from exc
    if db.scalar(select(Tenant).where(Tenant.name == body.name)):
        raise HTTPException(409, "tenant name already exists")
    tenant, key = create_tenant(db, body.name, list(body.allowed_tools), body.webhook_url)
    audit(db, tenant.id, "tenant.create", name=body.name, tools=list(body.allowed_tools))
    db.commit()
    return {
        "id": tenant.id,
        "name": tenant.name,
        "api_key": key,
        "webhook_secret": tenant.webhook_secret,
        "note": "Store the api_key now. It is not shown again.",
    }


@router.post(
    "/admin/tenants/{tenant_id}/keys",
    status_code=201,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
def admin_create_tenant_key(
    tenant_id: int, body: TenantKeyCreate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "tenant not found")
    key = new_api_key()
    row = TenantKey(tenant_id=tenant.id, key_hash=hash_key(key), role=body.role, label=body.label)
    db.add(row)
    db.flush()
    audit(db, tenant.id, "tenant_key.create", role=body.role, label=body.label, key_id=row.id)
    db.commit()
    return {
        "id": row.id,
        "tenant_id": tenant.id,
        "role": row.role,
        "label": row.label,
        "api_key": key,
        "note": "Store the api_key now. It is not shown again.",
    }


@router.get("/admin/tools", tags=["admin"], dependencies=[Depends(require_admin)])
def admin_tools() -> list[str]:
    return ALL_TOOLS


# ---- knowledge base ---------------------------------------------------------
@router.post("/v1/documents", status_code=201, tags=["knowledge base"])
def add_document(
    body: DocumentIn,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=200),
) -> Any:
    embedder = request.app.state.embedder

    def ingest() -> dict[str, Any]:
        n = ingest_document(db, tenant.id, embedder, body.name, body.text, body.access_label)
        audit(db, tenant.id, "document.ingest", name=body.name, chunks=n)
        return {"name": body.name, "chunks": n}

    if not idempotency_key:
        data = ingest()
        db.commit()
        return data
    body_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    stored, replayed = run_once(
        db,
        tenant.id,
        f"doc:{idempotency_key}",
        lambda: {**ingest(), "body_hash": body_hash},
        ttl_hours=request.app.state.settings.idempotency_ttl_hours,
    )
    if stored.get("body_hash") != body_hash:
        raise HTTPException(422, "Idempotency-Key was already used with a different body")
    data = {k: v for k, v in stored.items() if k != "body_hash"}
    headers = {"Idempotent-Replayed": "true"} if replayed else {}
    return Response(json.dumps(data), status_code=201, media_type="application/json", headers=headers)


@router.post("/v2/search", tags=["knowledge base"])
def search_v2(
    body: SearchV2In,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Search with a mode, filters and provenance. /v1/search stays as it is."""
    st = request.app.state
    role_labels = labels_for_role(getattr(request.state, "role", "agent"))
    wanted = frozenset(body.labels) if body.labels is not None else role_labels
    if not wanted <= role_labels:
        raise HTTPException(403, "label not allowed for this key")
    vector = st.embedder.embed([body.query])[0]
    query = SearchQuery(
        tenant_id=tenant.id,
        text=body.query,
        vector=vector,
        k=body.k,
        mode=body.mode,
        allowed_labels=wanted,
        doc_names=body.doc_names,
    )
    retriever = build_retriever(db, st.settings.retriever, st.settings.rrf_k)
    hits = retriever.search(query)
    docs = {
        d.id: d
        for d in db.scalars(
            select(Document).where(Document.id.in_({h.doc_id for h in hits}), Document.tenant_id == tenant.id)
        )
    }
    return {
        "api_version": "2",
        "mode": body.mode,
        "hits": [
            {
                "doc_id": h.doc_id,
                "doc": h.doc,
                "chunk": h.chunk,
                "label": h.label,
                "source_uri": docs[h.doc_id].source_uri if h.doc_id in docs else None,
                "ingested_at": docs[h.doc_id].created_at.isoformat() if h.doc_id in docs else None,
                "score": round(h.score, 6),
                "scores": {k: round(v, 6) for k, v in h.scores.items()},
                "ranks": dict(h.rank_by),
                "text": h.text,
            }
            for h in hits
        ],
    }


@router.post("/v1/documents/async", status_code=202, tags=["knowledge base"])
def add_document_async(
    body: DocumentIn,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=200),
) -> dict[str, Any]:
    """Store the text in the blob store and queue an ingest job. A worker does the rest."""
    ports = request.app.state.ports
    blob_key = f"tenants/{tenant.id}/{uuid.uuid4().hex}.txt"
    ports.blob.put(blob_key, body.text.encode(), "text/plain")
    job_id = ports.queue.send(
        {
            "tenant_id": tenant.id,
            "blob_key": blob_key,
            "name": body.name,
            "access_label": body.access_label,
            "idempotency_key": idempotency_key or job_key(tenant.id, blob_key),
        }
    )
    audit(db, tenant.id, "document.ingest_queued", name=body.name, job_id=job_id)
    db.commit()
    return {"job_id": job_id, "blob_key": blob_key, "status": "queued"}


@router.post("/v1/documents/upload", status_code=201, tags=["knowledge base"])
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file too large")
    name = file.filename or "upload"
    try:
        text = extract_text(name, data)
        n = ingest_document(db, tenant.id, request.app.state.embedder, name, text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # corrupt PDF etc.
        raise HTTPException(422, f"could not read file: {type(exc).__name__}") from exc
    audit(db, tenant.id, "document.ingest", name=name, chunks=n)
    db.commit()
    return {"name": name, "chunks": n}


@router.get("/v1/documents", tags=["knowledge base"])
def list_documents(
    tenant: Tenant = Depends(current_tenant), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    docs = db.scalars(select(Document).where(Document.tenant_id == tenant.id).order_by(Document.id)).all()
    return [{"id": d.id, "name": d.name} for d in docs]


@router.delete("/v1/documents/{doc_id}", status_code=204, tags=["knowledge base"])
def delete_document(
    doc_id: int, tenant: Tenant = Depends(current_tenant), db: Session = Depends(get_db)
) -> Response:
    doc = db.get(Document, doc_id)
    if not doc or doc.tenant_id != tenant.id:
        raise HTTPException(404, "document not found")
    db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    db.delete(doc)
    audit(db, tenant.id, "document.delete", name=doc.name)
    db.commit()
    return Response(status_code=204)


@router.post("/v1/search", tags=["knowledge base"])
def search(
    body: SearchIn,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    role = getattr(request.state, "role", "agent")
    hits = retrieve(db, tenant.id, request.app.state.embedder, body.query, body.k, role)
    return {
        "hits": [{"doc": h.doc, "chunk": h.chunk, "score": round(h.score, 4), "text": h.text} for h in hits]
    }


# ---- agent ------------------------------------------------------------------
@router.post("/v1/agent/run", tags=["agent"])
def agent_run(
    body: AgentIn,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> AgentResult:
    result = _agent(request, db, tenant, body.question)
    db.commit()
    return result


# ---- tickets ----------------------------------------------------------------
@router.post("/v1/tickets", status_code=201, tags=["tickets"])
def post_ticket(
    body: TicketIn,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=200),
) -> Any:
    def make() -> dict[str, Any]:
        t = create_ticket(db, tenant.id, body.subject, body.body)
        audit(db, tenant.id, "ticket.create", ticket_id=t.id)
        return _ticket_json(t)

    if idempotency_key:
        data, replayed = run_once(
            db, tenant.id, idempotency_key, make, ttl_hours=request.app.state.settings.idempotency_ttl_hours
        )
        return _json(data, 200 if replayed else 201, replayed)
    data = make()
    db.commit()
    return data


@router.get("/v1/tickets/{ticket_id}", tags=["tickets"])
def read_ticket(
    ticket_id: int, tenant: Tenant = Depends(current_tenant), db: Session = Depends(get_db)
) -> dict[str, Any]:
    t = get_ticket(db, tenant.id, ticket_id)
    if not t:
        raise HTTPException(404, "ticket not found")
    return _ticket_json(t)


@router.get("/v1/tickets", tags=["tickets"])
def list_tickets(
    tenant: Tenant = Depends(current_tenant), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = db.scalars(select(Ticket).where(Ticket.tenant_id == tenant.id).order_by(Ticket.id)).all()
    return [_ticket_json(t) for t in rows]


# ---- cases ------------------------------------------------------------------
@router.post("/v1/cases", status_code=201, tags=["cases"])
def post_case(
    body: CaseIn,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    service: CaseService = request.app.state.case_service
    role = cast(Literal["agent", "team_lead"], getattr(request.state, "role", "agent"))
    ticket = CaseTicket(subject=body.subject, body=body.body, order_id=body.order_id)
    state = service.start(db, tenant, role, ticket)
    return {
        "thread_id": state.thread_id,
        "status": state.status,
        "proposal": state.proposal.model_dump() if state.proposal else None,
        "needs_approval": state.needs_approval,
    }


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=500)


@router.get("/v1/cases", tags=["cases"])
def list_cases(
    request: Request,
    status: str | None = None,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    service: CaseService = request.app.state.case_service
    return [case_json(row) for row in service.list_cases(db, tenant, status)]


@router.post("/v1/cases/{thread_id}/decision", tags=["cases"])
def decide_case(
    thread_id: str,
    body: DecisionIn,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if getattr(request.state, "role", "agent") != "team_lead":
        raise HTTPException(403, "team_lead role required")
    service: CaseService = request.app.state.case_service
    row = service.get(db, tenant, thread_id)
    if row is None:
        raise HTTPException(404, "case not found")
    if row.status != "awaiting_approval":
        raise HTTPException(409, "case is not awaiting approval")
    actor = getattr(request.state, "key_label", "unknown")
    state = service.decide(db, tenant, thread_id, body.decision, actor, body.reason)
    return {"thread_id": thread_id, "status": state.status, "action_result": state.action_result}


@router.get("/v1/cases/{thread_id}", tags=["cases"])
def read_case(
    thread_id: str,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    service: CaseService = request.app.state.case_service
    row = service.get(db, tenant, thread_id)
    if row is None:
        raise HTTPException(404, "case not found")
    return case_json(row)


def _json(data: dict[str, Any], status: int, replayed: bool) -> Response:
    headers = {"Idempotent-Replay": "true"} if replayed else {}
    return Response(json.dumps(data), status_code=status, media_type="application/json", headers=headers)


# ---- inbound webhook --------------------------------------------------------
@router.post("/v1/webhooks/inbound/{tenant_id}", tags=["integrations"])
async def inbound_webhook(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_signature: str | None = Header(default=None),
    x_timestamp: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, max_length=200),
) -> Response:
    """Signed events from the customer's system. Creates a ticket and adds an agent triage note."""
    body = await request.body()
    tenant = db.get(Tenant, tenant_id)
    # Same error for "no such tenant" and "bad signature": do not reveal which tenant ids exist.
    if not tenant or not verify(tenant.webhook_secret, x_timestamp, x_signature, body):
        audit(db, tenant.id if tenant else None, "webhook.rejected", reason="bad_signature")
        db.commit()
        raise HTTPException(401, "invalid signature")
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header required")
    try:
        event = InboundEvent.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(422, f"invalid event: {exc.error_count()} problem(s)") from exc

    def handle() -> dict[str, Any]:
        t = create_ticket(db, tenant.id, event.subject, event.body)
        triage = _agent(
            request,
            db,
            tenant,
            f"{event.subject}\n{event.body}",
            only_tools={"search_knowledge_base"},
        )
        t.triage_note = triage.answer
        audit(db, tenant.id, "webhook.inbound", event=event.event, ticket_id=t.id)
        return {
            "ticket_id": t.id,
            "triage": {
                "status": triage.status,
                "answer": triage.answer,
                "citations": [c.model_dump() for c in triage.citations],
            },
        }

    data, replayed = run_once(
        db, tenant.id, idempotency_key, handle, ttl_hours=request.app.state.settings.idempotency_ttl_hours
    )
    return _json(data, 200, replayed)


# ---- CRM connector ----------------------------------------------------------
@router.post("/v1/connectors/crm/customers/{customer_id}/ingest", tags=["integrations"])
def crm_ingest(
    customer_id: str,
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Pull one customer from the CRM (OAuth2 client credentials) and index it as a document."""
    http = request.app.state.http
    tokens = ClientCredentialsTokenProvider(
        f"{settings.crm_base_url}/oauth/token",
        settings.crm_client_id,
        settings.crm_client_secret.get_secret_value(),
        client=http,
    )
    try:
        customer = CrmClient(settings.crm_base_url, tokens, http).get_customer(customer_id)
    except Exception as exc:
        raise HTTPException(502, f"CRM request failed: {type(exc).__name__}") from exc
    if customer is None:
        raise HTTPException(404, "customer not found in CRM")
    name = f"crm-customer-{customer_id}.md"
    n = ingest_document(db, tenant.id, request.app.state.embedder, name, customer_to_text(customer))
    audit(db, tenant.id, "connector.crm.ingest", customer_id=customer_id, chunks=n)
    db.commit()
    return {"document": name, "chunks": n}


# ---- audit ------------------------------------------------------------------
@router.get("/v1/audit", tags=["ops"])
def audit_log(
    limit: int = 50, tenant: Tenant = Depends(current_tenant), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant.id)
        .order_by(AuditLog.id.desc())
        .limit(min(max(limit, 1), 200))
    ).all()
    return [
        {
            "id": r.id,
            "action": r.action,
            "detail": r.detail,
            "request_id": r.request_id,
            "trace_id": r.trace_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
