"""The agent loop: ask the model, run the tools it asks for, repeat. With guardrails."""

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from aip.agent.tools import REGISTRY, ToolContext
from aip.audit import audit
from aip.models import AgentRun
from aip.observability import AGENT_RUNS, LLM_CALLS, TOOL_CALLS, redact, span, trace_id_var
from aip.providers.base import ChatProvider, Message, ProviderError

log = logging.getLogger("aip.agent")
CITATION_RE = re.compile(r"\[([^\[\]#]+#\d+)\]")


class AgentQuestion(BaseModel):
    """Input validation."""

    question: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def not_blank(self) -> "AgentQuestion":
        if not self.question.strip():
            raise ValueError("question is blank")
        return self


class Citation(BaseModel):
    doc: str
    chunk: int
    score: float


class AgentAnswer(BaseModel):
    """Output validation."""

    answer: str = Field(min_length=1, max_length=4000)
    citations: list[Citation] = Field(default_factory=list)


class AgentResult(BaseModel):
    run_id: int | None = None
    status: str  # ok | max_steps | provider_error | invalid_output
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    prompt_version: str = ""


def _validate_output(text: str, ctx: ToolContext) -> AgentAnswer:
    """The answer may only cite chunks that the agent really retrieved in this run."""
    cited = CITATION_RE.findall(text)
    unknown = [c for c in cited if c not in ctx.retrieved]
    if unknown:
        raise ValueError(f"answer cites sources that were not retrieved: {unknown}")
    cites = [Citation(**ctx.retrieved[c]) for c in dict.fromkeys(cited)]
    return AgentAnswer(answer=text.strip(), citations=cites)


def run_agent(
    provider: ChatProvider,
    ctx: ToolContext,
    question: str,
    system_prompt: str,
    prompt_version: str,
    *,
    max_steps: int,
    only_tools: set[str] | None = None,
) -> AgentResult:
    """Run one agent task. Adds rows to the session; the caller commits."""
    q = AgentQuestion(question=question).question
    tenant_id = ctx.tenant.id
    allowed = set(ctx.tenant.allowed_tools) & set(REGISTRY)
    if only_tools is not None:
        allowed &= only_tools
    specs = [REGISTRY[n].spec() for n in sorted(allowed)]
    messages = [Message(role="system", content=system_prompt), Message(role="user", content=q)]
    steps: list[dict[str, Any]] = []
    status, answer = "max_steps", "I could not finish within the step limit."
    citations: list[Citation] = []

    with span("agent.run", tenant_id=tenant_id):
        for step in range(max_steps):
            LLM_CALLS.labels(provider=provider.name).inc()
            try:
                with span("llm.chat", step=step):
                    resp = provider.chat(messages, specs)
            except ProviderError as exc:
                log.warning("provider error", extra={"fields": {"error": str(exc)}})
                status, answer = (
                    "provider_error",
                    "The language model is unavailable. Try again later.",
                )
                break
            if not resp.tool_calls:
                try:
                    out = _validate_output(resp.content, ctx)
                    status, answer, citations = "ok", out.answer, out.citations
                except (ValueError, ValidationError) as exc:
                    log.warning("invalid agent output", extra={"fields": {"error": str(exc)[:200]}})
                    status, answer = (
                        "invalid_output",
                        "The answer failed validation and was withheld.",
                    )
                break
            messages.append(Message(role="assistant", content=resp.content, tool_calls=resp.tool_calls))
            for call in resp.tool_calls:
                result = _run_tool(ctx, allowed, call.name, call.arguments, steps)
                messages.append(
                    Message(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content=json.dumps(result),
                    )
                )

    AGENT_RUNS.labels(status=status).inc()
    run = AgentRun(
        tenant_id=tenant_id,
        question=redact(q),
        answer=redact(answer),
        status=status,
        prompt_version=prompt_version,
        steps=redact(steps),
        trace_id=trace_id_var.get(),
    )
    ctx.db.add(run)
    ctx.db.flush()
    audit(ctx.db, tenant_id, "agent.run", run_id=run.id, status=status, steps=len(steps))
    return AgentResult(
        run_id=run.id,
        status=status,
        answer=answer,
        citations=citations,
        steps=steps,
        prompt_version=prompt_version,
    )


def _run_tool(
    ctx: ToolContext,
    allowed: set[str],
    name: str,
    args: dict[str, Any],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    tenant_id = ctx.tenant.id
    if name not in allowed:  # allow-list guardrail: also stops a model inventing tool names
        TOOL_CALLS.labels(tool=name if name in REGISTRY else "unknown", outcome="denied").inc()
        audit(ctx.db, tenant_id, "tool.denied", tool=name)
        steps.append({"tool": name, "outcome": "denied"})
        return {"error": f"tool '{name}' is not allowed for this tenant"}
    tool = REGISTRY[name]
    try:
        parsed = tool.args_model.model_validate(args)
    except ValidationError as exc:
        TOOL_CALLS.labels(tool=name, outcome="invalid_args").inc()
        steps.append({"tool": name, "outcome": "invalid_args"})
        return {"error": f"invalid arguments: {exc.error_count()} problem(s)"}
    try:
        with span("tool." + name):
            result = tool.run(ctx, parsed)
    except Exception as exc:  # a failing tool must not crash the whole run
        log.warning("tool failed", extra={"fields": {"tool": name, "error": type(exc).__name__}})
        TOOL_CALLS.labels(tool=name, outcome="error").inc()
        steps.append({"tool": name, "outcome": "error"})
        return {"error": f"tool failed: {type(exc).__name__}"}
    TOOL_CALLS.labels(tool=name, outcome="ok").inc()
    audit(ctx.db, tenant_id, "tool.call", tool=name, args=args)
    steps.append({"tool": name, "args": args, "outcome": "ok"})
    return result
