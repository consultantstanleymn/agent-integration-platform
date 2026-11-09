"""The MCP server exposes one write tool and refuses bad input."""

import os
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import select

from aip.db import make_engine, make_session_factory
from aip.models import ReturnAuthorization
from aip.tenants import create_tenant

ROOT = Path(__file__).resolve().parents[1]
GOOD = {
    "order_id": "ORD-1001",
    "amount_minor": 4500,
    "action": "refund",
    "reason": "damaged",
    "idempotency_key": "k1",
}


@pytest.fixture
def mcp_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'mcp.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    factory = make_session_factory(make_engine(url))
    with factory() as db:
        tenant, _ = create_tenant(db, "mcp-co", ["search_knowledge_base"], None)
        db.commit()
        tid = tenant.id
    return url, tid, factory


async def _call(url, tid, fn):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aip.mcp_server", "--tenant-id", str(tid)],
        env={**os.environ, "DATABASE_URL": url, "AIP_HARD_CAP_MINOR": "50000"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        return await fn(session)


@pytest.mark.anyio
async def test_lists_exactly_one_tool(mcp_db):
    url, tid, _ = mcp_db

    async def fn(s):
        return await s.list_tools()

    tools = (await _call(url, tid, fn)).tools
    assert [t.name for t in tools] == ["create_return_authorization"]


@pytest.mark.anyio
async def test_tool_schema_has_required_fields(mcp_db):
    url, tid, _ = mcp_db

    async def fn(s):
        return (await s.list_tools()).tools[0]

    tool = await _call(url, tid, fn)
    assert set(tool.inputSchema["required"]) == {
        "order_id",
        "amount_minor",
        "action",
        "reason",
        "idempotency_key",
    }
    assert "tenant_id" not in tool.inputSchema["properties"]


@pytest.mark.anyio
async def test_creates_ra_row(mcp_db):
    url, tid, factory = mcp_db

    async def fn(s):
        return await s.call_tool("create_return_authorization", GOOD)

    result = await _call(url, tid, fn)
    assert not result.isError
    with factory() as db:
        row = db.scalar(select(ReturnAuthorization))
        assert row.tenant_id == tid and row.amount_minor == 4500 and row.ra_number.startswith("RA-")


@pytest.mark.anyio
async def test_over_cap_is_error_and_no_row(mcp_db):
    url, tid, factory = mcp_db

    async def fn(s):
        return await s.call_tool("create_return_authorization", {**GOOD, "amount_minor": 50001})

    assert (await _call(url, tid, fn)).isError
    with factory() as db:
        assert db.scalar(select(ReturnAuthorization)) is None


@pytest.mark.anyio
async def test_bad_order_id_is_error(mcp_db):
    url, tid, factory = mcp_db

    async def fn(s):
        return await s.call_tool("create_return_authorization", {**GOOD, "order_id": "1001; DROP"})

    assert (await _call(url, tid, fn)).isError
    with factory() as db:
        assert db.scalar(select(ReturnAuthorization)) is None


@pytest.mark.anyio
async def test_same_idempotency_key_returns_same_ra(mcp_db):
    url, tid, factory = mcp_db

    async def fn(s):
        a = await s.call_tool("create_return_authorization", GOOD)
        b = await s.call_tool("create_return_authorization", GOOD)
        return a, b

    a, b = await _call(url, tid, fn)
    assert a.structuredContent["ra_number"] == b.structuredContent["ra_number"]
    with factory() as db:
        assert len(db.scalars(select(ReturnAuthorization)).all()) == 1


@pytest.mark.anyio
async def test_tenant_comes_from_server_not_input(mcp_db):
    url, tid, factory = mcp_db

    async def fn(s):
        return await s.call_tool("create_return_authorization", {**GOOD, "tenant_id": 999})

    assert (await _call(url, tid, fn)).isError
    with factory() as db:
        assert db.scalar(select(ReturnAuthorization)) is None
