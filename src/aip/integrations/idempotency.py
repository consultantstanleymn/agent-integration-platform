from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aip.models import IdempotencyKey


def run_once(
    db: Session, tenant_id: int, key: str, fn: Callable[[], dict[str, Any]]
) -> tuple[dict[str, Any], bool]:
    """Run fn once per (tenant, key). Return (response, replayed).

    fn must only add/flush rows, never commit. This helper commits the work and the key together,
    so a crash leaves neither. If two requests race, the unique constraint lets only one win.
    """
    query = select(IdempotencyKey).where(IdempotencyKey.tenant_id == tenant_id, IdempotencyKey.key == key)
    row = db.scalar(query)
    if row:
        return row.response, True
    result = fn()
    db.add(IdempotencyKey(tenant_id=tenant_id, key=key, response=result))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(query)
        if row is None:
            raise
        return row.response, True
    return result, False
