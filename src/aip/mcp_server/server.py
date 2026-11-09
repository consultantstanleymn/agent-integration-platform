"""One MCP server, one write tool: ``create_return_authorization``.

The tenant is bound when the server starts. It never comes from tool input.
The amount limit, the order id pattern and the action list are checked here.
"""

import uuid
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aip.audit import audit
from aip.models import ReturnAuthorization

TOOL_NAME = "create_return_authorization"


class ReturnAuthorizationInput(BaseModel):
    """Validated tool input. Unknown fields (for example ``tenant_id``) are rejected."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(pattern=r"^ORD-\d{1,12}$")
    amount_minor: int = Field(ge=0)
    action: str = Field(pattern=r"^(refund|replacement|store_credit)$")
    reason: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=100)


def create_ra(
    session_factory: Callable[[], Session],
    tenant_id: int,
    hard_cap_minor: int,
    data: ReturnAuthorizationInput,
) -> dict[str, object]:
    """Insert one return authorization. The same idempotency key returns the same row."""
    if data.amount_minor > hard_cap_minor:
        raise ValueError(f"amount_minor is above the hard cap of {hard_cap_minor}")
    with session_factory() as db:
        existing = _find(db, tenant_id, data.idempotency_key)
        if existing is not None:
            return _result(existing, replayed=True)
        row = ReturnAuthorization(
            tenant_id=tenant_id,
            ra_number=f"RA-{uuid.uuid4().hex[:12].upper()}",
            order_id=data.order_id,
            amount_minor=data.amount_minor,
            action=data.action,
            reason=data.reason,
            idempotency_key=data.idempotency_key,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            again = _find(db, tenant_id, data.idempotency_key)
            if again is None:
                raise
            return _result(again, replayed=True)
        audit(
            db,
            tenant_id,
            "mcp.create_return_authorization",
            ra_number=row.ra_number,
            order_id=row.order_id,
            amount_minor=row.amount_minor,
            ra_action=row.action,
        )
        db.commit()
        return _result(row, replayed=False)


def _find(db: Session, tenant_id: int, key: str) -> ReturnAuthorization | None:
    return db.scalar(
        select(ReturnAuthorization).where(
            ReturnAuthorization.tenant_id == tenant_id, ReturnAuthorization.idempotency_key == key
        )
    )


def _result(row: ReturnAuthorization, *, replayed: bool) -> dict[str, object]:
    return {
        "ra_number": row.ra_number,
        "order_id": row.order_id,
        "amount_minor": row.amount_minor,
        "action": row.action,
        "replayed": replayed,
    }


def build_server(tenant_id: int, session_factory: Callable[[], Session], hard_cap_minor: int) -> FastMCP:
    server = FastMCP("aip-returns")

    @server.tool(name=TOOL_NAME)
    def create_return_authorization(
        order_id: str, amount_minor: int, action: str, reason: str, idempotency_key: str
    ) -> dict[str, object]:
        """Create one return authorization for the tenant bound to this server."""
        data = ReturnAuthorizationInput(
            order_id=order_id,
            amount_minor=amount_minor,
            action=action,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        return create_ra(session_factory, tenant_id, hard_cap_minor, data)

    # Reject unknown arguments such as ``tenant_id``: the tenant is never a tool input.
    arg_model = server._tool_manager.get_tool(TOOL_NAME).fn_metadata.arg_model  # type: ignore[union-attr]
    arg_model.model_config["extra"] = "forbid"
    arg_model.model_rebuild(force=True)
    return server
