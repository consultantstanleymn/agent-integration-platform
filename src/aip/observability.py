"""Structured JSON logs, request/trace ids, PII redaction, Prometheus metrics, tiny spans."""

import json
import logging
import re
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from prometheus_client import Counter, Histogram

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
tenant_id_var: ContextVar[int | None] = ContextVar("tenant_id", default=None)

HTTP_REQUESTS = Counter("aip_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("aip_http_request_seconds", "HTTP latency", ["route"])
AGENT_RUNS = Counter("aip_agent_runs_total", "Agent runs", ["status"])
TOOL_CALLS = Counter("aip_tool_calls_total", "Tool calls", ["tool", "outcome"])
LLM_CALLS = Counter("aip_llm_calls_total", "LLM chat calls", ["provider"])

# ---- PII redaction -------------------------------------------------------
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:aip|sk)[_-][A-Za-z0-9_-]{16,}"), "[SECRET]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [SECRET]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[CARD]"),
    (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[PHONE]"),
]
_SKIP_KEYS = {"trace_id", "request_id", "span_id"}


def redact(value: Any) -> Any:
    """Replace emails, phones, SSNs, card numbers and secrets. Works on nested data."""
    if isinstance(value, str):
        for pattern, repl in _PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: v if k in _SKIP_KEYS else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": request_id_var.get(),
            "trace_id": trace_id_var.get(),
            "tenant_id": tenant_id_var.get(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(redact(fields))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def new_trace_id() -> str:
    return secrets.token_hex(16)


def parse_traceparent(header: str | None) -> str | None:
    """W3C traceparent: version-traceid-spanid-flags. Return the 32-hex trace id or None."""
    if not header:
        return None
    m = re.fullmatch(r"[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}", header.strip())
    return m.group(1) if m and m.group(1) != "0" * 32 else None


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    """A very small trace span: logs name, span id, duration. Not exported to a collector."""
    span_id = secrets.token_hex(8)
    start = time.perf_counter()
    try:
        yield
    finally:
        ms = round((time.perf_counter() - start) * 1000, 2)
        logging.getLogger("aip.trace").info(
            "span", extra={"fields": {"span": name, "span_id": span_id, "duration_ms": ms, **attrs}}
        )
