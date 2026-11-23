"""Proves the agent actually talks to the tools MCP server over the
protocol: schema discovery via list_tools(), and a tool call round-trip,
with no in-process shortcut.
"""
from app.mcp_client import get_mcp_client
from app.tools import all_tool_schemas, execute_tool, reset_test_state


def setup_function():
    reset_test_state()


def test_schemas_are_discovered_from_the_mcp_server():
    schemas = all_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"get_booking", "get_flight_status", "search_alternatives", "read_policy", "rebook_itinerary"}
    # admin/test-only tool must never be offered to the model
    assert "reset_mock_state" not in names


def test_get_booking_round_trips_over_mcp():
    result = execute_tool("get_booking", {"pnr": "ABC123"})
    assert result["passenger_name"] == "Ajay George"


def test_unknown_tool_over_mcp_is_reported_as_an_error():
    result = execute_tool("delete_everything", {})
    assert result["error"] is True


def test_client_is_a_process_wide_singleton():
    assert get_mcp_client() is get_mcp_client()
