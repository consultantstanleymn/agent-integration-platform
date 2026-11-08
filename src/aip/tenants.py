import secrets

from sqlalchemy.orm import Session

from aip.models import Tenant
from aip.security import hash_key, new_api_key

ALL_TOOLS = ["search_knowledge_base", "lookup_ticket", "create_ticket", "call_customer_webhook"]


def create_tenant(
    db: Session, name: str, allowed_tools: list[str] | None = None, webhook_url: str | None = None
) -> tuple[Tenant, str]:
    """Create a tenant. Returns (tenant, plaintext_api_key). The key is not recoverable later."""
    key = new_api_key()
    tenant = Tenant(
        name=name,
        api_key_hash=hash_key(key),
        webhook_secret="whsec_" + secrets.token_urlsafe(24),
        webhook_url=webhook_url,
        allowed_tools=allowed_tools
        if allowed_tools is not None
        else ["search_knowledge_base", "lookup_ticket"],
    )
    db.add(tenant)
    db.flush()
    return tenant, key
