"""Eval harness: load a YAML test set, run the agent on each question, score, report.

Scoring per case:
  - fact check: every expected fact (case-insensitive substring) appears in the answer
  - citation check: the answer cites the expected source document (or cites nothing, if none expected)
  - tool check (optional): the expected tool was used
A case passes only if all its checks pass.
"""

from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field

from aip.agent.loop import AgentResult, run_agent
from aip.agent.prompts import load_prompt
from aip.agent.tools import ToolContext
from aip.config import Settings
from aip.db import make_engine, make_session_factory
from aip.models import Base
from aip.providers import build_chat_provider, build_embedder
from aip.rag.service import ingest_document
from aip.tenants import ALL_TOOLS, create_tenant
from aip.tickets import create_ticket


class Case(BaseModel):
    id: str
    question: str
    expected_facts: list[str] = Field(default_factory=list)
    expected_source: str | None = None
    expected_tool: str | None = None


class TestSet(BaseModel):
    __test__ = False  # not a pytest class
    kb_dir: str
    tickets: list[dict[str, str]] = Field(default_factory=list)
    cases: list[Case]


def score_case(case: Case, result: AgentResult) -> dict[str, Any]:
    answer = result.answer.lower()
    missing = [f for f in case.expected_facts if f.lower() not in answer]
    cited = {c.doc for c in result.citations}
    cite_ok = (case.expected_source in cited) if case.expected_source else not cited
    used = {s["tool"] for s in result.steps if s.get("outcome") == "ok"}
    tool_ok = case.expected_tool in used if case.expected_tool else True
    return {
        "id": case.id,
        "passed": result.status == "ok" and not missing and cite_ok and tool_ok,
        "status": result.status,
        "facts_ok": not missing,
        "missing_facts": missing,
        "citation_ok": cite_ok,
        "tool_ok": tool_ok,
        "answer": result.answer,
    }


def run_evals(testset_path: Path, settings: Settings, prompt_version: str | None = None) -> dict[str, Any]:
    ts = TestSet.model_validate(yaml.safe_load(testset_path.read_text()))
    version = prompt_version or settings.prompt_version
    prompt = load_prompt(settings.prompts_dir, version)
    engine = make_engine("sqlite://")  # fresh in-memory database for every run
    Base.metadata.create_all(engine)
    chat, embedder = build_chat_provider(settings), build_embedder(settings)
    results: list[dict[str, Any]] = []
    with make_session_factory(engine)() as db:
        tenant, _ = create_tenant(db, "eval", list(ALL_TOOLS))
        for f in sorted((testset_path.parent / ts.kb_dir).glob("*")):
            ingest_document(db, tenant.id, embedder, f.name, f.read_text())
        for t in ts.tickets:
            create_ticket(db, tenant.id, t["subject"], t.get("body", ""))
        with httpx.Client() as http:
            for case in ts.cases:
                ctx = ToolContext(db=db, tenant=tenant, embedder=embedder, http=http, settings=settings)
                res = run_agent(chat, ctx, case.question, prompt, version, max_steps=settings.max_agent_steps)
                results.append(score_case(case, res))
    passed = sum(1 for r in results if r["passed"])
    return {
        "prompt_version": version,
        "provider": chat.name,
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "cases": results,
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [f"Eval report  provider={report['provider']}  prompt={report['prompt_version']}"]
    for c in report["cases"]:
        mark = "PASS" if c["passed"] else "FAIL"
        extra = (
            ""
            if c["passed"]
            else f"  missing={c['missing_facts']} citation_ok={c['citation_ok']} tool_ok={c['tool_ok']}"
        )
        lines.append(f"  [{mark}] {c['id']}{extra}")
    lines.append(f"Pass rate: {report['passed']}/{report['total']} = {report['pass_rate'] * 100:.1f}%")
    return "\n".join(lines)
