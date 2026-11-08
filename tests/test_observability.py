import json
import logging

from aip.observability import JsonFormatter, parse_traceparent, redact, request_id_var, trace_id_var
from aip.security import RateLimiter


def test_redact_pii_and_secrets():
    s = redact(
        "mail jane@example.com call +1 (415) 555-0199 ssn 123-45-6789 card 4111 1111 1111 1111 "
        "key aip_abcdefghijklmnopqrstuvwxyz Bearer abc.def.ghi"
    )
    for leaked in [
        "jane@example.com",
        "555-0199",
        "123-45-6789",
        "4111",
        "abcdefghijklmnop",
        "abc.def.ghi",
    ]:
        assert leaked not in s
    assert "[EMAIL]" in s and "[SSN]" in s and "[CARD]" in s and "[SECRET]" in s


def test_redact_nested_and_keeps_ids():
    d = redact({"a": ["x@y.io"], "trace_id": "0123456789abcdef0123456789abcdef"})
    assert d["a"] == ["[EMAIL]"] and d["trace_id"].startswith("0123")


def test_json_log_has_request_and_trace_ids_and_redacts():
    request_id_var.set("rid-1")
    trace_id_var.set("t" * 32)
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "user %s", ("bob@x.com",), None)
    rec.fields = {"phone": "415-555-0100 0000"}
    out = json.loads(JsonFormatter().format(rec))
    assert out["request_id"] == "rid-1" and out["trace_id"] == "t" * 32
    assert "bob@x.com" not in json.dumps(out)


def test_parse_traceparent():
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parse_traceparent(f"00-{tid}-00f067aa0ba902b7-01") == tid
    assert parse_traceparent("garbage") is None and parse_traceparent(None) is None


def test_request_id_and_trace_headers(env):
    r = env.client.get("/health", headers={"X-Request-Id": "abc-1"})
    assert r.headers["X-Request-Id"] == "abc-1" and len(r.headers["X-Trace-Id"]) == 32
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    r = env.client.get(
        "/health",
        headers={"traceparent": f"00-{tid}-00f067aa0ba902b7-01", "X-Request-Id": "bad id!"},
    )
    assert r.headers["X-Trace-Id"] == tid and r.headers["X-Request-Id"] != "bad id!"


def test_metrics_endpoint(env):
    t = env.tenant()
    env.client.get("/v1/documents", headers=t["headers"])
    text = env.client.get("/metrics").text
    assert 'aip_http_requests_total{method="GET",route="/v1/documents",status="200"}' in text
    assert "aip_agent_runs_total" in text or "aip_http_request_seconds" in text


def test_rate_limiter_window():
    now = [0.0]
    rl = RateLimiter(2, window=10, clock=lambda: now[0])
    assert rl.check("k") == 0 and rl.check("k") == 0
    assert rl.check("k") > 0
    assert rl.check("other") == 0
    now[0] = 11
    assert rl.check("k") == 0
