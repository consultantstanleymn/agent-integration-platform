import hmac
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from aip.config import Settings
from aip.models import Tenant, TenantKey
from aip.observability import tenant_id_var
from aip.security import hash_key


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def current_tenant(
    request: Request,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Tenant:
    key = x_api_key
    if not key and authorization and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    if not key:
        raise HTTPException(401, "missing API key")
    key_hash = hash_key(key)
    tenant = db.scalar(select(Tenant).where(Tenant.api_key_hash == key_hash))
    if tenant:
        role = "agent"
        key_label = "legacy"
    else:
        tenant_key = db.scalar(select(TenantKey).where(TenantKey.key_hash == key_hash))
        if not tenant_key:
            raise HTTPException(401, "invalid API key")
        tenant = db.get(Tenant, tenant_key.tenant_id)
        if not tenant:
            raise HTTPException(401, "invalid API key")
        role = tenant_key.role
        key_label = tenant_key.label
    tenant_id_var.set(tenant.id)
    request.state.tenant_id = tenant.id  # sync deps run in a thread copy of the context
    request.state.role = role
    request.state.key_label = key_label
    wait = request.app.state.rate_limiter.check(f"tenant:{tenant.id}")
    if wait:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(int(wait) + 1)})
    return tenant


def require_admin(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.admin_token.get_secret_value()
    if not expected or not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
        raise HTTPException(401, "admin token required")
