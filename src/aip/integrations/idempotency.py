from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aip.models import IdempotencyKey

DEFAULT_TTL_HOURS = 72


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _is_expired(row: IdempotencyKey, now: datetime) -> bool:
    return row.expires_at is not None and _aware(row.expires_at) <= now


def run_once(
    db: Session,
    tenant_id: int,
    key: str,
    fn: Callable[[], dict[str, Any]],
    *,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Run fn once per (tenant, key). Return (response, replayed).

    fn must only add/flush rows, never commit. This helper commits the work and the key together,
    so a crash leaves neither. If two requests race, the unique constraint lets only one win.
    A key past its expiry counts as absent: the old row is replaced and fn runs again.
    """
    moment = now or datetime.now(UTC)
    query = select(IdempotencyKey).where(IdempotencyKey.tenant_id == tenant_id, IdempotencyKey.key == key)
    row = db.scalar(query)
    if row and not _is_expired(row, moment):
        return row.response, True
    if row:
        db.delete(row)
        db.flush()
    result = fn()
    db.add(
        IdempotencyKey(
            tenant_id=tenant_id,
            key=key,
            response=result,
            expires_at=moment + timedelta(hours=ttl_hours),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(query)
        if row is None:
            raise
        return row.response, True
    return result, False


def purge_expired(db: Session, now: datetime | None = None) -> int:
    """Delete keys past their expiry. Keys without an expiry (older rows) stay. Returns the count."""
    moment = now or datetime.now(UTC)
    ids = [
        r.id
        for r in db.scalars(select(IdempotencyKey).where(IdempotencyKey.expires_at.is_not(None)))
        if _is_expired(r, moment)
    ]
    if ids:
        db.execute(delete(IdempotencyKey).where(IdempotencyKey.id.in_(ids)))
        db.commit()
    return len(ids)
