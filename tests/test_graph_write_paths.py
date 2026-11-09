"""The case graph has one write path: the MCP tool behind the action port."""

import ast
from pathlib import Path

from aip.mcp_server.server import TOOL_NAME

GRAPH_DIR = Path(__file__).resolve().parents[1] / "src" / "aip" / "graph"
FORBIDDEN_IMPORTS = {"httpx", "requests"}
FORBIDDEN_NAMES = {"create_ticket", "call_customer_webhook", "REGISTRY"}


def _modules():
    return {p.name: ast.parse(p.read_text()) for p in sorted(GRAPH_DIR.glob("*.py"))}


def _imported(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
            names.update(a.name for a in node.names)
    return names


def test_graph_package_imports_no_write_tools():
    for name, tree in _modules().items():
        imported = _imported(tree)
        assert not imported & FORBIDDEN_IMPORTS, f"{name} imports an HTTP client"
        assert not imported & {"create_ticket", "call_customer_webhook"}, f"{name} imports a write tool"
        if name != "service.py":
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr != "add", f"{name} writes to a session"


def test_graph_has_no_tool_registry_access():
    for name, tree in _modules().items():
        imported = _imported(tree)
        assert "REGISTRY" not in imported, f"{name} imports the tool registry"
        source = (GRAPH_DIR / name).read_text()
        assert "aip.agent.tools" not in source, f"{name} reaches the agent tools"


def test_mcp_server_exposes_one_tool_name():
    from aip.mcp_server.server import build_server

    server = build_server(1, lambda: None, 100)  # type: ignore[arg-type,return-value]
    names = [t.name for t in server._tool_manager.list_tools()]
    assert names == [TOOL_NAME] == ["create_return_authorization"]
