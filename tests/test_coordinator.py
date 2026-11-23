from app import state
from app.coordinator import run_coordinator
from app.tools import execute_tool, reset_test_state
from tests.fakes import constant_client, final_response, scripted_client, tool_call_response


def setup_function():
    reset_test_state()


def test_coordinator_dispatches_parallel_tasks_then_escalates_high_value_rebook():
    coordinator_client = scripted_client(
        [
            tool_call_response(
                [
                    ("Task", {"subagent_type": "status-analyst", "prompt": "check PNR ABC123"}),
                    ("Task", {"subagent_type": "options-finder", "prompt": "find alternatives for AA123"}),
                ]
            ),
            tool_call_response(
                [("rebook_itinerary", {"pnr": "ABC123", "new_flight_number": "AA129", "fare_difference_usd": 1250})]
            ),
            final_response("Your rebooking needs approval and has been escalated as a ticket."),
        ]
    )
    # Subagents never need to call their own tools for this test -- they just
    # report a canned finding immediately (stop_reason=end_turn on first call).
    subagent_client = constant_client(lambda _payload: final_response("mock subagent finding"))

    result = run_coordinator(
        "PNR ABC123, rebook me onto the business cabin even with a fare difference.",
        client=coordinator_client,
        subagent_client=subagent_client,
    )

    dispatched_types = {sc.subagent_type for sc in result.subagent_calls}
    assert dispatched_types == {"status-analyst", "options-finder"}
    assert all(sc.findings == "mock subagent finding" for sc in result.subagent_calls)

    assert len(result.tool_calls) == 1
    rebook_call = result.tool_calls[0]
    assert rebook_call.name == "rebook_itinerary"
    assert rebook_call.denied_by_hook is True
    assert rebook_call.result["errorCategory"] == "permission"

    assert len(state.ESCALATION_TICKETS) == 1
    assert state.ESCALATION_TICKETS[0]["fare_difference_usd"] == 1250

    assert "escalated" in result.final_text.lower()
    assert not result.hit_iteration_cap


def test_coordinator_allows_low_value_rebook_without_escalation():
    coordinator_client = scripted_client(
        [
            tool_call_response([("Task", {"subagent_type": "status-analyst", "prompt": "check PNR ABC123"})]),
            tool_call_response(
                [("rebook_itinerary", {"pnr": "ABC123", "new_flight_number": "AA125", "fare_difference_usd": 0})]
            ),
            final_response("You're rebooked on AA125 at no extra cost."),
        ]
    )
    subagent_client = constant_client(lambda _payload: final_response("mock finding"))

    result = run_coordinator(
        "PNR ABC123, just rebook me on AA125.",
        client=coordinator_client,
        subagent_client=subagent_client,
    )

    assert result.tool_calls[0].denied_by_hook is False
    assert result.tool_calls[0].result["status"] == "confirmed"
    assert state.ESCALATION_TICKETS == []

    # The rebook happened on the MCP server's own booking backend -- verify
    # through the same tools interface rather than reaching into a
    # module-level dict that only mirrors the host process.
    booking = execute_tool("get_booking", {"pnr": "ABC123"})
    assert booking["segments"][0]["flight_number"] == "AA125"
