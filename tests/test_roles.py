"""Tenant keys with roles, and the 0002 migration."""

from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from starlette.requests import Request

from aip.api.deps import current_tenant

ROOT = Path(__file__).resolve().parents[1]


def _create_key(env, tenant_id: int, role: str, label: str = ""):
    return env.client.post(
        f"/admin/tenants/{tenant_id}/keys",
        json={"role": role, "label": label},
        headers={"X-Admin-Token": "admin-secret"},
    )


def _auth(env, key: str) -> tuple[int, str]:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/documents",
            "headers": [(b"x-api-key", key.encode())],
            "app": env.app,
        }
    )
    with Session(env.engine) as db:
        tenant = current_tenant(request, db=db, x_api_key=key)
        return tenant.id, request.state.role


def test_admin_creates_team_lead_key(env):
    t = env.tenant()
    r = _create_key(env, t["id"], "team_lead", "Lead")

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tenant_id"] == t["id"]
    assert body["role"] == "team_lead"
    assert body["label"] == "Lead"
    assert body["api_key"].startswith("aip_")

    actions = {a["action"] for a in env.client.get("/v1/audit", headers=t["headers"]).json()}
    assert "tenant_key.create" in actions


def test_team_lead_key_authenticates_with_role(env):
    t = env.tenant()
    key = _create_key(env, t["id"], "team_lead", "Lead").json()["api_key"]

    assert env.client.get("/v1/documents", headers={"X-API-Key": key}).status_code == 200
    assert _auth(env, key) == (t["id"], "team_lead")


def test_legacy_key_role_is_agent(env):
    t = env.tenant()

    assert env.client.get("/v1/documents", headers=t["headers"]).status_code == 200
    assert _auth(env, t["api_key"]) == (t["id"], "agent")


def test_invalid_role_is_422(env):
    t = env.tenant()

    assert _create_key(env, t["id"], "owner", "Owner").status_code == 422


def test_migration_0002_downgrade(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    db = f"sqlite:///{tmp_path / 'm2.db'}"
    monkeypatch.setenv("DATABASE_URL", db)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))

    command.upgrade(cfg, "0002")
    tables = set(inspect(create_engine(db)).get_table_names())
    assert {"cases", "tenant_keys"} <= tables

    command.downgrade(cfg, "0001")
    tables = set(inspect(create_engine(db)).get_table_names())
    assert "cases" not in tables
    assert "tenant_keys" not in tables
    assert "tenants" in tables
