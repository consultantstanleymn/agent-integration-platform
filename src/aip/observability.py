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

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
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
_SKIP_KEYS = {"trace_id", "request_id", "span_id", "thread_id", "ra_number"}


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


_tracer: trace.Tracer = trace.get_tracer("aip")
_SKIP_ATTRS = {
    "question",
    "text",
    "subject",
    "body",
    "query",
    "answer",
    "reason",
}  # personal text never goes in spans


def setup_tracing(
    service_name: str, endpoint: str | None = None, provider: TracerProvider | None = None
) -> TracerProvider:
    """Create the tracer provider. With an endpoint, spans go to an OTLP HTTP collector in batches.

    Pass ``provider`` to use your own (tests use an in-memory exporter). Without an endpoint spans are
    created and dropped, which costs little.
    """
    global _tracer
    if provider is None:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        if endpoint:
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_traces_url(endpoint))))
    _tracer = provider.get_tracer("aip")
    return provider


def _traces_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base if base.endswith("/v1/traces") else base + "/v1/traces"


def get_tracer() -> trace.Tracer:
    return _tracer


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def _set_attrs(sp: trace.Span, attrs: dict[str, Any]) -> None:
    for key, value in attrs.items():
        if key in _SKIP_ATTRS:
            continue
        if isinstance(value, str | int | float | bool):
            sp.set_attribute(key, value)


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[trace.Span]:
    """Open an OpenTelemetry span and also log its name and duration."""
    span_id = secrets.token_hex(8)
    start = time.perf_counter()
    with _tracer.start_as_current_span(name) as sp:
        _set_attrs(sp, attrs)
        try:
            yield sp
        finally:
            ms = round((time.perf_counter() - start) * 1000, 2)
            logging.getLogger("aip.trace").info(
                "span", extra={"fields": {"span": name, "span_id": span_id, "duration_ms": ms, **attrs}}
            )


@contextmanager
def llm_span(system: str, model: str | None) -> Iterator[trace.Span]:
    """A span for one chat call with GenAI attributes. Call ``record_usage`` after the reply."""
    name = f"chat {model or system}"
    with _tracer.start_as_current_span(name, kind=trace.SpanKind.CLIENT) as sp:
        sp.set_attribute("gen_ai.system", system)
        sp.set_attribute("gen_ai.request.model", model or "unknown")
        sp.set_attribute("gen_ai.operation.name", "chat")
        yield sp


def record_usage(sp: trace.Span, input_tokens: int, output_tokens: int) -> None:
    sp.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    sp.set_attribute("gen_ai.usage.output_tokens", output_tokens)
