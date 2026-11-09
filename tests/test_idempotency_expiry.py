"""Idempotency keys expire and can be purged."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from aip.integrations.idempotency import purge_expired, run_once
from aip.models import IdempotencyKey


def test_expired_key_is_treated_as_new(db_session):
    calls = []

    def fn():
        calls.append(1)
        return {"n": len(calls)}

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    first, replayed = run_once(db_session, 1, "k", fn, ttl_hours=1, now=t0)
    assert (first, replayed) == ({"n": 1}, False)
    again, replayed = run_once(db_session, 1, "k", fn, ttl_hours=1, now=t0 + timedelta(minutes=30))
    assert (again, replayed) == ({"n": 1}, True)
    later, replayed = run_once(db_session, 1, "k", fn, ttl_hours=1, now=t0 + timedelta(hours=2))
    assert (later, replayed) == ({"n": 2}, False)
    assert len(calls) == 2


def test_purge_removes_only_expired(db_session):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    run_once(db_session, 1, "old", lambda: {"a": 1}, ttl_hours=1, now=t0)
    run_once(db_session, 1, "new", lambda: {"a": 2}, ttl_hours=100, now=t0)
    db_session.add(IdempotencyKey(tenant_id=1, key="legacy", response={}, expires_at=None))
    db_session.commit()
    assert purge_expired(db_session, now=t0 + timedelta(hours=5)) == 1
    keys = {r.key for r in db_session.scalars(select(IdempotencyKey))}
    assert keys == {"new", "legacy"}
