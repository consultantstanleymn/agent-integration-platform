from aip.models import AuditLog, Tenant


def test_health_and_openapi(env):
    assert env.client.get("/health").json() == {"status": "ok"}
    spec = env.client.get("/openapi.json").json()
    assert "/v1/agent/run" in spec["paths"]


def test_admin_requires_token(env):
    r = env.client.post("/admin/tenants", json={"name": "x"})
    assert r.status_code == 401
    r = env.client.post("/admin/tenants", json={"name": "x"}, headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_api_key_never_stored_in_plaintext(env):
    t = env.tenant()
    from sqlalchemy.orm import Session

    with Session(env.engine) as s:
        row = s.query(Tenant).one()
        assert row.api_key_hash != t["api_key"]
        assert t["api_key"] not in row.api_key_hash


def test_missing_and_bad_key_rejected(env):
    env.tenant()
    assert env.client.get("/v1/documents").status_code == 401
    assert env.client.get("/v1/documents", headers={"X-API-Key": "aip_nope"}).status_code == 401


def test_bearer_header_also_works(env):
    t = env.tenant()
    r = env.client.get("/v1/documents", headers={"Authorization": f"Bearer {t['api_key']}"})
    assert r.status_code == 200


def test_duplicate_tenant_name_conflict(env):
    env.tenant("dup")
    r = env.client.post("/admin/tenants", json={"name": "dup"}, headers={"X-Admin-Token": "admin-secret"})
    assert r.status_code == 409


def test_unknown_tool_name_rejected_at_tenant_create(env):
    r = env.client.post(
        "/admin/tenants",
        json={"name": "z", "allowed_tools": ["rm_rf"]},
        headers={"X-Admin-Token": "admin-secret"},
    )
    assert r.status_code == 422


def test_private_webhook_url_blocked_when_not_allowed(env):
    env.app.state.settings.allow_private_webhook_targets = False
    r = env.client.post(
        "/admin/tenants",
        json={"name": "z", "webhook_url": "http://127.0.0.1/x"},
        headers={"X-Admin-Token": "admin-secret"},
    )
    assert r.status_code == 422


def test_rate_limit_returns_429(env):
    from aip.security import RateLimiter

    t = env.tenant()
    env.app.state.rate_limiter = RateLimiter(2)
    codes = [env.client.get("/v1/documents", headers=t["headers"]).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_audit_log_records_actions_and_request_id(env):
    t = env.tenant()
    env.add_kb(t)
    r = env.client.get("/v1/audit", headers={**t["headers"], "X-Request-Id": "req-123"})
    actions = {a["action"] for a in r.json()}
    assert {"tenant.create", "document.ingest"} <= actions
    from sqlalchemy.orm import Session

    env.client.post(
        "/v1/documents",
        json={"name": "b.md", "text": "hello world"},
        headers={**t["headers"], "X-Request-Id": "req-456"},
    )
    with Session(env.engine) as s:
        assert s.query(AuditLog).filter_by(request_id="req-456").count() == 1
