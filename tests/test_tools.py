"""Fast, in-process tests of the tool business logic (mcp_server/tool_impl.py).

These import the implementation module directly -- no MCP subprocess, no
network -- to keep the core-logic test suite fast. The MCP transport
itself (schema discovery + real tool calls over stdio) is covered
separately in tests/test_mcp_integration.py.
"""
from app import mock_data
from mcp_server import tool_impl


def setup_function():
    mock_data.reset_mock_state()


def test_get_booking_valid():
    booking = tool_impl.get_booking("ABC123")
    assert booking["passenger_name"] == "Ajay George"
    assert booking["segments"][0]["flight_number"] == "AA123"


def test_get_booking_invalid_format_is_validation_error():
    try:
        tool_impl.get_booking("1")
        assert False, "expected a ToolError"
    except Exception as e:
        assert e.error_category == "validation"
        assert e.is_retryable is False


def test_get_booking_unknown_pnr_is_validation_error():
    try:
        tool_impl.get_booking("ZZZZZZ")
        assert False, "expected a ToolError"
    except Exception as e:
        assert e.error_category == "validation"


def test_get_flight_status_transient_then_success():
    try:
        tool_impl.get_flight_status("AA999", "2026-11-26")
        assert False, "expected a transient ToolError on first call"
    except Exception as e:
        assert e.error_category == "transient"
        assert e.is_retryable is True

    second = tool_impl.get_flight_status("AA999", "2026-11-26")
    assert second["status"] == "on_time"


def test_search_alternatives_filters_by_cabin():
    result = tool_impl.search_alternatives("JFK", "LHR", "2026-11-24", cabin_class="Business")
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["flight_number"] == "AA129"


def test_search_alternatives_bad_airport_code_is_validation_error():
    try:
        tool_impl.search_alternatives("J", "LHR", "2026-11-24")
        assert False, "expected a ToolError"
    except Exception as e:
        assert e.error_category == "validation"


def test_read_policy_valid_topic_has_citation():
    policy = tool_impl.read_policy("hotel")
    assert "hotel" in policy["clause"].lower()
    assert policy["source"] == "irrops_policy.md v3.2"


def test_read_policy_unknown_topic_is_validation_error():
    try:
        tool_impl.read_policy("refunds")
        assert False, "expected a ToolError"
    except Exception as e:
        assert e.error_category == "validation"


def test_rebook_itinerary_updates_booking():
    result = tool_impl.rebook_itinerary("ABC123", "AA125", 0)
    assert result["status"] == "confirmed"
    assert mock_data.BOOKINGS["ABC123"]["segments"][0]["flight_number"] == "AA125"


def test_rebook_itinerary_unknown_pnr_is_validation_error():
    try:
        tool_impl.rebook_itinerary("ZZZZZZ", "AA1", 0)
        assert False, "expected a ToolError"
    except Exception as e:
        assert e.error_category == "validation"
