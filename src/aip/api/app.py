import logging
import re
import time
import uuid

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import Engine

from aip.api.routes import router
from aip.config import Settings
from aip.db import install_audit_triggers, make_engine, make_session_factory
from aip.graph.actions import build_action_port
from aip.graph.authority import load_authority
from aip.graph.service import CaseService
from aip.observability import (
    HTTP_LATENCY,
    HTTP_REQUESTS,
    configure_logging,
    new_trace_id,
    parse_traceparent,
    request_id_var,
    tenant_id_var,
    trace_id_var,
)
from aip.ports import Ports, build_ports
from aip.providers import build_chat_provider, build_embedder
from aip.providers.base import ChatProvider, Embedder
from aip.providers.pricing import load_prices
from aip.security import RateLimiter

log = logging.getLogger("aip.http")


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    chat: ChatProvider | None = None,
    embedder: Embedder | None = None,
    http: httpx.Client | None = None,
    ports: Ports | None = None,
) -> FastAPI:
    """Build the app. Tests pass fakes for everything that touches the outside world."""
    settings = settings or Settings()
    app = FastAPI(
        title="Agent Integration Platform",
        version="0.1.0",
        description="Support triage agent platform: RAG, tool use, webhooks, evals.",
    )
    app.state.settings = settings
    app.state.engine = engine or make_engine(settings.database_url)
    install_audit_triggers(app.state.engine)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.chat = chat or build_chat_provider(settings)
    app.state.embedder = embedder or build_embedder(settings)
    app.state.http = http or httpx.Client(timeout=10)
    app.state.ports = ports or build_ports(settings)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    _authority = load_authority(settings.authority_path)
    app.state.case_service = CaseService(
        checkpoint_url=settings.checkpoint_url,
        provider=app.state.chat,
        embedder=app.state.embedder,
        approval_limit_minor=settings.approval_limit_minor,
        max_steps=settings.case_max_steps,
        max_tokens=settings.case_max_tokens,
        authority=_authority,
        prices=load_prices(settings.llm_price_per_mtok_json),
        action_port=build_action_port(
            settings.action_port, str(settings.database_url), _authority.hard_cap_minor
        ),
    )

    @app.middleware("http")
    async def observe(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id", "")
        rid = rid if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", rid) else uuid.uuid4().hex
        request_id_var.set(rid)
        trace_id_var.set(parse_traceparent(request.headers.get("traceparent")) or new_trace_id())
        tenant_id_var.set(None)
        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            response = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
        elapsed = time.perf_counter() - start
        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_LATENCY.labels(route).observe(elapsed)
        response.headers["X-Request-Id"] = rid
        response.headers["X-Trace-Id"] = trace_id_var.get()
        tenant_id_var.set(getattr(request.state, "tenant_id", None))
        log.info(
            "request",
            extra={
                "fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 2),
                }
            },
        )
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(router)
    return app


def app_factory() -> FastAPI:
    """Entry point for uvicorn: uvicorn --factory aip.api.app:app_factory"""
    configure_logging()
    return create_app()
