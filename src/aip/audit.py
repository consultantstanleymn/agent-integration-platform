from typing import Any

from sqlalchemy.orm import Session

from aip.models import AuditLog
from aip.observability import request_id_var, trace_id_var
from aip.redaction import Redactor, default_redactor


def audit(
    db: Session,
    tenant_id: int | None,
    action: str,
    *,
    redactor: Redactor | None = None,
    **detail: Any,
) -> None:
    """Add an audit row (redacted). The caller commits."""
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            request_id=request_id_var.get(),
            trace_id=trace_id_var.get(),
            action=action,
            detail=(redactor or default_redactor()).redact(detail),
        )
    )
