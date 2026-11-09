"""The write side of the case graph: one port, two adapters.

Both adapters end in the same validation and insert function from
``aip.mcp_server.server``. The MCP adapter reaches it through a stdio server
process. The in-process adapter calls it directly, for unit tests.
"""

import asyncio
import os
import sys
from collections.abc import Callable
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy.orm import Session

from aip.graph.state import Proposal
from aip.mcp_server.server import TOOL_NAME, ReturnAuthorizationInput, create_ra


class ActionPort(Protocol):
    def create_return_authorization(
        self, tenant_id: int, proposal: Proposal, idempotency_key: str
    ) -> dict[str, Any]: ...


def _arguments(proposal: Proposal, idempotency_key: str) -> dict[str, Any]:
    return {
        "order_id": proposal.order_id,
        "amount_minor": proposal.amount_minor,
        "action": proposal.action,
        "reason": proposal.reason,
        "idempotency_key": idempotency_key,
    }


class InProcessActionPort:
    def __init__(self, session_factory: Callable[[], Session], hard_cap_minor: int) -> None:
        self.session_factory = session_factory
        self.hard_cap_minor = hard_cap_minor

    def create_return_authorization(
        self, tenant_id: int, proposal: Proposal, idempotency_key: str
    ) -> dict[str, Any]:
        data = ReturnAuthorizationInput(**_arguments(proposal, idempotency_key))
        return dict(create_ra(self.session_factory, tenant_id, self.hard_cap_minor, data))


class McpActionPort:
    """Starts the MCP server over stdio for each call and calls the one write tool."""

    def __init__(self, database_url: str, hard_cap_minor: int) -> None:
        self.database_url = database_url
        self.hard_cap_minor = hard_cap_minor

    def create_return_authorization(
        self, tenant_id: int, proposal: Proposal, idempotency_key: str
    ) -> dict[str, Any]:
        return asyncio.run(self._call(tenant_id, _arguments(proposal, idempotency_key)))

    async def _call(self, tenant_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "aip.mcp_server", "--tenant-id", str(tenant_id)],
            env={
                **os.environ,
                "DATABASE_URL": self.database_url,
                "AIP_HARD_CAP_MINOR": str(self.hard_cap_minor),
            },
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(TOOL_NAME, arguments)
        if result.isError or result.structuredContent is None:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise RuntimeError(f"MCP tool call failed: {text}")
        return dict(result.structuredContent)


def build_action_port(action_port: str, database_url: str, hard_cap_minor: int) -> ActionPort | None:
    """Return the MCP port for ``mcp``. Return None for ``inprocess``: the service builds that one."""
    if action_port == "mcp":
        return McpActionPort(database_url, hard_cap_minor)
    return None
