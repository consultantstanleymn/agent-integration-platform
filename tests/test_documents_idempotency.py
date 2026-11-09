"""POST /v1/documents with an Idempotency-Key."""

from sqlalchemy import select

from aip.db import make_session_factory
from aip.models import Document


def _count(env):
    with make_session_factory(env.engine)() as db:
        return len(db.scalars(select(Document)).all())


def _post(env, tenant, name="a.md", text="# A\n\nSome policy text.", key=None):
    headers = dict(tenant["headers"])
    if key:
        headers["Idempotency-Key"] = key
    return env.client.post("/v1/documents", json={"name": name, "text": text}, headers=headers)


def test_same_key_same_body_one_document(env):
    t = env.tenant()
    first = _post(env, t, key="k1")
    second = _post(env, t, key="k1")
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json() == {"name": "a.md", "chunks": 1}
    assert "idempotent-replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert _count(env) == 1


def test_same_key_other_body_422(env):
    t = env.tenant()
    assert _post(env, t, key="k1").status_code == 201
    other = _post(env, t, text="# A\n\nDifferent text.", key="k1")
    assert other.status_code == 422
    assert _count(env) == 1


def test_no_key_keeps_old_behavior(env):
    t = env.tenant()
    first = _post(env, t)
    second = _post(env, t)  # same name again replaces the document, as before
    assert first.status_code == second.status_code == 201
    assert "idempotent-replayed" not in second.headers
    assert _count(env) == 1


def test_keys_are_per_tenant(env):
    a, b = env.tenant("ta"), env.tenant("tb")
    assert _post(env, a, key="same").status_code == 201
    second = _post(env, b, key="same")
    assert second.status_code == 201 and "idempotent-replayed" not in second.headers
    assert _count(env) == 2
