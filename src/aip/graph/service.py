"""Start and read cases over a shared checkpointed graph.

``CaseService`` builds the graph dependencies from the running application,
keeps one checkpointer for the process, and persists the ``Case`` row after a
run. The graph itself stays free of database and HTTP details.
"""

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from aip.audit import audit
from aip.graph.authority import Authority
from aip.graph.build import build_graph, postgres_checkpointer, resume_case, run_case
from aip.graph.nodes import NodeDeps
from aip.graph.state import CaseState, Ticket
from aip.models import Case, Tenant
from aip.providers.base import ChatProvider, Embedder
from aip.rag.service import retrieve
from aip.rag.store import Hit


@dataclass
class CheckpointerHandle:
    saver: BaseCheckpointSaver
    close: Callable[[], None]


def sqlite_path(url: str) -> str:
    """Turn a ``sqlite://`` URL into the filesystem path the saver opens."""
    if url.startswith("sqlite:////"):
        return "/" + url[len("sqlite:////") :]
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        return url[len("sqlite://") :]
    return url


def open_checkpointer(url: str) -> CheckpointerHandle:
    """Open the saver named by ``checkpoint_url``. Postgres URLs use the Postgres saver."""
    if url.startswith("postgres"):
        stack = ExitStack()
        saver = stack.enter_context(postgres_checkpointer(url))
        return CheckpointerHandle(saver=saver, close=stack.close)
    connection = sqlite3.connect(sqlite_path(url), check_same_thread=False)
    return CheckpointerHandle(saver=SqliteSaver(connection), close=connection.close)


def upsert_case(db: Session, state: CaseState) -> Case:
    """Insert or update the ``Case`` row for one graph run."""
    row = db.scalar(select(Case).where(Case.thread_id == state.thread_id))
    if row is None:
        row = Case(tenant_id=state.tenant_id, thread_id=state.thread_id)
        db.add(row)
    row.status = state.status
    row.category = state.category
    row.proposal = state.proposal.model_dump() if state.proposal else None
    row.decision = state.decision.model_dump() if state.decision else None
    row.steps_used = state.steps_used
    row.tokens_used = state.tokens_used
    db.flush()
    return row


def case_json(row: Case) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "thread_id": row.thread_id,
        "status": row.status,
        "category": row.category,
        "proposal": row.proposal,
        "decision": row.decision,
        "steps_used": row.steps_used,
        "tokens_used": row.tokens_used,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


class CaseService:
    """Build and drive the case graph with the application's adapters."""

    def __init__(
        self,
        *,
        checkpoint_url: str,
        provider: ChatProvider,
        embedder: Embedder,
        approval_limit_minor: int,
        max_steps: int = 6,
        max_tokens: int = 4000,
        authority: Authority | None = None,
    ) -> None:
        self.checkpoint_url = checkpoint_url
        self.provider = provider
        self.embedder = embedder
        self.approval_limit_minor = approval_limit_minor
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.authority = authority
        self._handle: CheckpointerHandle | None = None

    def _checkpointer(self) -> BaseCheckpointSaver:
        if self._handle is None:
            self._handle = open_checkpointer(self.checkpoint_url)
        return self._handle.saver

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _deps(self, db: Session) -> NodeDeps:
        def retrieve_hits(tenant_id: int, query: str, k: int) -> list[Hit]:
            return retrieve(db, tenant_id, self.embedder, query, k)

        def record_state(state: CaseState) -> None:
            upsert_case(db, state)
            audit(
                db,
                state.tenant_id,
                "case.recorded",
                thread_id=state.thread_id,
                status=state.status,
                refusal_reason=state.refusal_reason,
            )
            db.commit()

        return NodeDeps(
            provider=self.provider,
            retrieve=retrieve_hits,
            approval_limit_minor=self.approval_limit_minor,
            act=lambda state: {"stub": True},
            record=record_state,
            max_steps=self.max_steps,
            max_tokens=self.max_tokens,
            authority=self.authority,
        )

    def start(
        self,
        db: Session,
        tenant: Tenant,
        role: Literal["agent", "team_lead"],
        ticket: Ticket,
    ) -> CaseState:
        state = CaseState(
            tenant_id=tenant.id,
            thread_id=uuid.uuid4().hex,
            role=role,
            ticket=ticket,
        )
        graph = build_graph(self._deps(db), self._checkpointer())
        result = run_case(graph, state)
        upsert_case(db, result)
        db.commit()
        return result

    def decide(
        self,
        db: Session,
        tenant: Tenant,
        thread_id: str,
        decision: Literal["approve", "reject"],
        actor: str,
        reason: str = "",
    ) -> CaseState:
        """Resume a paused case with a team lead decision. The caller checks the role and the status."""
        graph = build_graph(self._deps(db), self._checkpointer())
        result = resume_case(graph, thread_id, {"decision": decision, "actor": actor, "reason": reason})
        upsert_case(db, result)
        audit(
            db,
            tenant.id,
            "case.approved" if decision == "approve" else "case.rejected",
            thread_id=thread_id,
            actor=actor,
            reason=reason,
        )
        db.commit()
        return result

    def list_cases(self, db: Session, tenant: Tenant, status: str | None = None) -> list[Case]:
        query = select(Case).where(Case.tenant_id == tenant.id).order_by(Case.id)
        if status:
            query = query.where(Case.status == status)
        return list(db.scalars(query))

    def get(self, db: Session, tenant: Tenant, thread_id: str) -> Case | None:
        return db.scalar(select(Case).where(Case.thread_id == thread_id, Case.tenant_id == tenant.id))
