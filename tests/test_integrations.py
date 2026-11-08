import json

import httpx
import pytest

from aip.integrations.crm import CrmClient
from aip.integrations.oauth import ClientCredentialsTokenProvider
from aip.integrations.webhooks import (
    UnsafeTargetError,
    send_webhook,
    sign,
    validate_target_url,
    verify,
)


def test_sign_verify_roundtrip_and_tamper():
    body = b'{"a":1}'
    sig = sign("s3cret", "1000", body)
    assert verify("s3cret", "1000", sig, body, now=1000)
    assert not verify("s3cret", "1000", sig, b'{"a":2}', now=1000)
    assert not verify("other", "1000", sig, body, now=1000)


def test_verify_rejects_old_timestamp_and_missing_headers():
    sig = sign("k", "1000", b"x")
    assert not verify("k", "1000", sig, b"x", now=1000 + 301)
    assert not verify("k", None, sig, b"x")
    assert not verify("k", "1000", None, b"x")
    assert not verify("k", "abc", sig, b"x")


def test_inbound_webhook_creates_ticket_with_triage(env):
    t = env.tenant()
    env.add_kb(t)
    body, h = env.signed(
        t, {"event": "ticket.created", "subject": "Refund", "body": "How long for a full refund?"}
    )
    r = env.client.post(
        f"/v1/webhooks/inbound/{t['id']}", content=body, headers={**h, "Idempotency-Key": "k1"}
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "30 days" in data["triage"]["answer"]
    ticket = env.client.get(f"/v1/tickets/{data['ticket_id']}", headers=t["headers"]).json()
    assert ticket["triage_note"] == data["triage"]["answer"]


def test_inbound_webhook_bad_signature_rejected_and_audited(env):
    t = env.tenant()
    body, h = env.signed(t, {"event": "ticket.created", "subject": "s"})
    h["X-Signature"] = "sha256=" + "0" * 64
    r = env.client.post(
        f"/v1/webhooks/inbound/{t['id']}", content=body, headers={**h, "Idempotency-Key": "k"}
    )
    assert r.status_code == 401
    assert env.client.get("/v1/tickets", headers=t["headers"]).json() == []
    actions = [a["action"] for a in env.client.get("/v1/audit", headers=t["headers"]).json()]
    assert "webhook.rejected" in actions


def test_inbound_webhook_replayed_old_timestamp_rejected(env):
    import time

    t = env.tenant()
    body, h = env.signed(t, {"event": "ticket.created", "subject": "s"}, ts=time.time() - 3600)
    r = env.client.post(
        f"/v1/webhooks/inbound/{t['id']}", content=body, headers={**h, "Idempotency-Key": "k"}
    )
    assert r.status_code == 401


def test_inbound_webhook_unknown_tenant_same_error(env):
    r = env.client.post(
        "/v1/webhooks/inbound/999", content=b"{}", headers={"X-Signature": "x", "X-Timestamp": "1"}
    )
    assert r.status_code == 401


def test_inbound_webhook_idempotent(env):
    t = env.tenant()
    body, h = env.signed(t, {"event": "ticket.created", "subject": "once"})
    hdr = {**h, "Idempotency-Key": "same-key"}
    r1 = env.client.post(f"/v1/webhooks/inbound/{t['id']}", content=body, headers=hdr)
    r2 = env.client.post(f"/v1/webhooks/inbound/{t['id']}", content=body, headers=hdr)
    assert r1.json() == r2.json()
    assert r2.headers["Idempotent-Replay"] == "true" and "Idempotent-Replay" not in r1.headers
    assert len(env.client.get("/v1/tickets", headers=t["headers"]).json()) == 1


def test_inbound_webhook_needs_idempotency_key_and_valid_event(env):
    t = env.tenant()
    body, h = env.signed(t, {"event": "ticket.created", "subject": "s"})
    assert env.client.post(f"/v1/webhooks/inbound/{t['id']}", content=body, headers=h).status_code == 400
    body, h = env.signed(t, {"event": "nope"})
    r = env.client.post(
        f"/v1/webhooks/inbound/{t['id']}", content=body, headers={**h, "Idempotency-Key": "z"}
    )
    assert r.status_code == 422


def test_idempotency_keys_are_per_tenant(env):
    a, b = env.tenant("a"), env.tenant("b")
    for t in (a, b):
        r = env.client.post(
            "/v1/tickets", json={"subject": "s"}, headers={**t["headers"], "Idempotency-Key": "k"}
        )
        assert r.status_code == 201
    r = env.client.post(
        "/v1/tickets", json={"subject": "s"}, headers={**a["headers"], "Idempotency-Key": "k"}
    )
    assert r.status_code == 200 and r.headers["Idempotent-Replay"] == "true"


# ---- outbound ---------------------------------------------------------------
def _client(responses):
    seen = []

    def handler(req):
        seen.append(req)
        r = responses[min(len(seen) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return httpx.Response(r)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_outbound_retries_5xx_with_exponential_backoff():
    client, seen = _client([503, 500, 200])
    sleeps: list[float] = []
    res = send_webhook(
        "http://x.test/h", "s", {"a": 1}, client=client, sleep=sleeps.append, idempotency_key="id1"
    )
    assert res.ok and res.attempts == 3 and sleeps == [0.5, 1.0]
    assert all(r.headers["idempotency-key"] == "id1" for r in seen)
    assert verify("s", seen[0].headers["x-timestamp"], seen[0].headers["x-signature"], seen[0].content)


def test_outbound_retries_network_errors_then_gives_up():
    client, seen = _client([httpx.ConnectError("boom")])
    sleeps: list[float] = []
    res = send_webhook("http://x.test/h", "s", {}, client=client, max_attempts=3, sleep=sleeps.append)
    assert not res.ok and res.attempts == 3 and len(seen) == 3 and len(sleeps) == 2


def test_outbound_does_not_retry_4xx_but_retries_429():
    client, seen = _client([400])
    assert send_webhook("http://x.test", "s", {}, client=client, sleep=lambda s: None).attempts == 1
    client, seen = _client([429, 200])
    assert send_webhook("http://x.test", "s", {}, client=client, sleep=lambda s: None).ok


def test_url_validation():
    for bad in [
        "ftp://x.test",
        "http://127.0.0.1/x",
        "http://10.0.0.5/x",
        "http://localhost/x",
        "http://169.254.169.254/",
    ]:
        with pytest.raises(UnsafeTargetError):
            validate_target_url(bad)
    validate_target_url("https://example.com/hook")
    validate_target_url("http://127.0.0.1/x", allow_private=True)


# ---- oauth + connector ------------------------------------------------------
def test_oauth_caches_token_and_refreshes_near_expiry():
    now = [0.0]
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"access_token": f"tok{len(calls)}", "expires_in": 100})

    tp = ClientCredentialsTokenProvider(
        "http://a.test/token",
        "id",
        "sec",
        scope="read",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: now[0],
    )
    assert tp.get_token() == "tok1" and tp.get_token() == "tok1" and len(calls) == 1
    assert b"grant_type=client_credentials" in calls[0].content and b"scope=read" in calls[0].content
    assert calls[0].headers["authorization"].startswith("Basic ")
    now[0] = 80  # inside the 30s skew window
    assert tp.get_token() == "tok2"


def test_oauth_error_raises():
    from aip.integrations.oauth import OAuthError

    tp = ClientCredentialsTokenProvider(
        "http://a.test/t",
        "i",
        "s",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))),
    )
    with pytest.raises(OAuthError):
        tp.get_token()


def test_crm_client_against_real_mock_crm_app():
    from fastapi.testclient import TestClient
    from mock_crm.app import app as crm_app

    http = TestClient(crm_app, base_url="http://crm.test")
    tp = ClientCredentialsTokenProvider(
        "http://crm.test/oauth/token", "demo-client", "demo-secret", client=http
    )  # type: ignore[arg-type]
    crm = CrmClient("http://crm.test", tp, http)  # type: ignore[arg-type]
    assert crm.get_customer("c-100")["name"] == "Acme Dental"  # type: ignore[index]
    assert crm.get_customer("nope") is None
    tp.invalidate()  # next call fetches a fresh token
    assert crm.get_customer("c-200")["status"] == "past_due"  # type: ignore[index]


def test_mock_crm_rejects_bad_credentials_and_missing_token():
    from fastapi.testclient import TestClient
    from mock_crm.app import app as crm_app

    c = TestClient(crm_app)
    assert (
        c.post(
            "/oauth/token", data={"grant_type": "client_credentials"}, auth=("demo-client", "wrong")
        ).status_code
        == 401
    )
    assert c.get("/customers/c-100").status_code == 401


def test_crm_ingest_endpoint_end_to_end(env):
    from fastapi.testclient import TestClient
    from mock_crm.app import app as crm_app

    crm_http = TestClient(crm_app, base_url="http://crm.test")
    env.app.state.http = crm_http
    s = env.app.state.settings
    s.crm_base_url, s.crm_client_id = "http://crm.test", "demo-client"
    from pydantic import SecretStr

    s.crm_client_secret = SecretStr("demo-secret")
    t = env.tenant()
    r = env.client.post("/v1/connectors/crm/customers/c-200/ingest", headers=t["headers"])
    assert r.status_code == 200, r.text
    ans = env.client.post(
        "/v1/agent/run",
        json={"question": "Who is the account manager for Blue Harbor Clinic?"},
        headers=t["headers"],
    ).json()
    assert "Marcus Lee" in ans["answer"] and ans["citations"][0]["doc"] == "crm-customer-c-200.md"
    assert env.client.post("/v1/connectors/crm/customers/zzz/ingest", headers=t["headers"]).status_code == 404
    json.dumps(ans)
