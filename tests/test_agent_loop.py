"""Exercises the generic agent loop against the REAL tools MCP server
(app.tools.execute_tool now talks to mcp_server/server.py over stdio) --
only the model is faked. This is the integration point that proves tool
calls actually travel over MCP, not just an in-process function call.
"""
from app.agent_loop import run_agent_loop
from app.tools import GET_BOOKING_SCHEMA, GET_FLIGHT_STATUS_SCHEMA, reset_test_state
from tests.fakes import final_response, scripted_client, tool_call_response


def setup_function():
    reset_test_state()


def test_loop_handles_tool_use_then_end_turn():
    # get_flight_status fails transiently on its first call per flight/date
    # key (mock_data simulates a flaky upstream feed), so the scripted model
    # retries it once before getting a usable result -- matching what the
    # status-analyst system prompt instructs it to do.
    client = scripted_client(
        [
            tool_call_response([("get_booking", {"pnr": "ABC123"})]),
            tool_call_response([("get_flight_status", {"flight_number": "AA123", "date": "2026-11-24"})]),
            tool_call_response([("get_flight_status", {"flight_number": "AA123", "date": "2026-11-24"})]),
            final_response("AA123 is cancelled."),
        ]
    )

    result = run_agent_loop(
        system_prompt="status-analyst",
        user_message="PNR ABC123, is my flight ok?",
        tool_schemas=[GET_BOOKING_SCHEMA, GET_FLIGHT_STATUS_SCHEMA],
        client=client,
    )

    assert result.final_text == "AA123 is cancelled."
    assert not result.hit_iteration_cap
    assert [tc.name for tc in result.tool_calls] == ["get_booking", "get_flight_status", "get_flight_status"]
    assert result.tool_calls[1].result["errorCategory"] == "transient"
    assert result.tool_calls[2].result["status"] == "cancelled"

    tool_messages = [m for m in result.messages if m["role"] == "tool"]
    assert len(tool_messages) == 3


def test_loop_retries_transient_error_when_model_calls_tool_again():
    client = scripted_client(
        [
            tool_call_response([("get_flight_status", {"flight_number": "AA999", "date": "2026-11-26"})]),
            tool_call_response([("get_flight_status", {"flight_number": "AA999", "date": "2026-11-26"})]),
            final_response("AA999 is on time."),
        ]
    )

    result = run_agent_loop(
        system_prompt="status-analyst",
        user_message="status please",
        tool_schemas=[GET_FLIGHT_STATUS_SCHEMA],
        client=client,
    )

    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].result["errorCategory"] == "transient"
    assert result.tool_calls[1].result.get("error") is None
    assert result.final_text == "AA999 is on time."


def test_loop_stops_at_iteration_cap_instead_of_looping_forever():
    infinite_tool_calls = [tool_call_response([("get_booking", {"pnr": "ABC123"})]) for _ in range(10)]
    client = scripted_client(infinite_tool_calls)

    result = run_agent_loop(
        system_prompt="status-analyst",
        user_message="PNR ABC123",
        tool_schemas=[GET_BOOKING_SCHEMA],
        client=client,
        max_iterations=3,
    )

    assert result.hit_iteration_cap is True
    assert len(result.tool_calls) == 3
