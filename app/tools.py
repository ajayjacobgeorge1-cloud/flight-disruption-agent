"""Client-side facade over the tools MCP server.

Agent code still calls `execute_tool(name, arguments)` exactly as before --
only now that call goes out over MCP to mcp_server/server.py instead of
running an in-process function. Tool schemas are no longer hand-written
here: they're discovered from the MCP server's list_tools() response, then
exposed under the same module-level names (GET_BOOKING_SCHEMA, etc.) other
modules already import, via a lazy PEP 562 module __getattr__ -- so
subagents.py and coordinator.py needed no changes for this migration.
"""
from __future__ import annotations

from typing import Any, Optional

from app import state
from app.errors import validation
from app.mcp_client import get_mcp_client

_SCHEMA_CACHE: Optional[dict[str, dict[str, Any]]] = None

_EXPORTED_SCHEMA_NAMES = {
    "GET_BOOKING_SCHEMA": "get_booking",
    "GET_FLIGHT_STATUS_SCHEMA": "get_flight_status",
    "SEARCH_ALTERNATIVES_SCHEMA": "search_alternatives",
    "READ_POLICY_SCHEMA": "read_policy",
    "REBOOK_ITINERARY_SCHEMA": "rebook_itinerary",
}

# Server-side tools that exist for eval/test plumbing only -- never offered
# to the model as something it can call.
_ADMIN_ONLY_TOOLS = {"reset_mock_state"}


def _load_schemas() -> dict[str, dict[str, Any]]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        client = get_mcp_client()
        _SCHEMA_CACHE = {s["function"]["name"]: s for s in client.list_tool_schemas()}
    return _SCHEMA_CACHE


def __getattr__(name: str) -> dict[str, Any]:
    tool_name = _EXPORTED_SCHEMA_NAMES.get(name)
    if tool_name is None:
        raise AttributeError(f"module 'app.tools' has no attribute {name!r}")
    return _load_schemas()[tool_name]


def all_tool_schemas() -> list[dict[str, Any]]:
    """Every model-facing tool schema (admin/test-only tools excluded)."""
    return [s for name, s in _load_schemas().items() if name not in _ADMIN_ONLY_TOOLS]


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Runs a tool over MCP, normalizing any failure into the same
    structured-error dict a tool implementation itself would raise."""
    try:
        client = get_mcp_client()
    except Exception as exc:
        return validation(f"Could not reach the tools MCP server: {exc}", tool=name).to_dict()
    return client.call_tool(name, arguments)


def reset_test_state() -> None:
    """Test/eval helper: resets both the MCP server's mock backend and the
    host-side escalation-ticket queue."""
    execute_tool("reset_mock_state", {})
    state.reset_host_state()
