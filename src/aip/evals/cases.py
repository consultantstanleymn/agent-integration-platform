"""Case evals: run support tickets through the case graph and score the outcome.

Every case runs on a fresh in-memory database with the in-process action port.
A case tagged ``approve: true`` or ``approve: false`` gets a team lead decision
after the graph pauses. The score checks status, action and the approval flag.
"""

import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from aip.config import Settings
from aip.db import make_engine, make_session_factory
from aip.graph.authority import load_authority
from aip.graph.service import CaseService
from aip.graph.state import Ticket
from aip.models import Base
from aip.providers import build_chat_provider, build_embedder
from aip.providers.base import ChatProvider
from aip.rag.service import ingest_document
from aip.tenants import create_tenant


class CaseEval(BaseModel):
    id: str
    category: Literal["answer", "refusal", "injection", "approval", "budget"]
    ticket: dict[str, Any]
    role: Literal["agent", "team_lead"] = "agent"
    expect_status: str
    expect_action: str | None = None
    expect_needs_approval: bool | None = None
    approve: bool | None = None
    max_steps: int | None = None
    max_tokens: int | None = None


class CaseSuite(BaseModel):
    kb_dir: str
    cases: list[CaseEval] = Field(min_length=1)


def load_suite(path: Path) -> CaseSuite:
    return CaseSuite.model_validate(yaml.safe_load(path.read_text()))


def score(case: CaseEval, status: str, action: str | None, needs_approval: bool) -> dict[str, Any]:
    problems = []
    if status != case.expect_status:
        problems.append(f"status {status} != {case.expect_status}")
    if case.expect_action is not None and action != case.expect_action:
        problems.append(f"action {action} != {case.expect_action}")
    if case.expect_needs_approval is not None and needs_approval != case.expect_needs_approval:
        problems.append(f"needs_approval {needs_approval} != {case.expect_needs_approval}")
    return {
        "id": case.id,
        "category": case.category,
        "passed": not problems,
        "status": status,
        "action": action,
        "needs_approval": needs_approval,
        "problems": problems,
    }


def run_case_suite(path: Path, settings: Settings, chat: ChatProvider | None = None) -> dict[str, Any]:
    suite = load_suite(path)
    provider = chat or build_chat_provider(settings)
    embedder = build_embedder(settings)
    authority = load_authority(settings.authority_path)
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    results: list[dict[str, Any]] = []
    with make_session_factory(engine)() as db:
        tenant, _ = create_tenant(db, "case-eval", ["search_knowledge_base"])
        for doc in sorted((path.parent / suite.kb_dir).glob("*.md")):
            ingest_document(db, tenant.id, embedder, doc.name, doc.read_text())
        db.commit()
        for case in suite.cases:
            service = CaseService(
                checkpoint_url="sqlite:///:memory:",
                provider=provider,
                embedder=embedder,
                approval_limit_minor=authority.approval_limit_minor,
                max_steps=case.max_steps or settings.case_max_steps,
                max_tokens=case.max_tokens or settings.case_max_tokens,
                authority=authority,
            )
            try:
                state = service.start(db, tenant, case.role, Ticket(**case.ticket))
                if state.status == "awaiting_approval" and case.approve is not None:
                    decision: Literal["approve", "reject"] = "approve" if case.approve else "reject"
                    state = service.decide(db, tenant, state.thread_id, decision, "eval-lead")
            except Exception as exc:  # noqa: BLE001  a missing cassette or provider error fails the case, not the run
                results.append(
                    {"id": case.id, "category": case.category, "passed": False, "problems": [repr(exc)]}
                )
                continue
            finally:
                service.close()
            action = state.proposal.action if state.proposal else None
            results.append(score(case, state.status, action, state.needs_approval))
    passed = sum(1 for r in results if r["passed"])
    return {
        "suite": "cases",
        "eval_set_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "prompt_version": "case-graph",
        "provider": provider.name,
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4),
        "cases": results,
    }


def format_case_report(report: dict[str, Any]) -> str:
    lines = [f"Case eval report  provider={report['provider']}"]
    for c in report["cases"]:
        mark = "PASS" if c["passed"] else "FAIL"
        extra = "" if c["passed"] else "  " + "; ".join(c["problems"])
        lines.append(f"  [{mark}] {c['id']}{extra}")
    lines.append(f"Pass rate: {report['passed']}/{report['total']} = {report['pass_rate'] * 100:.1f}%")
    return "\n".join(lines)
