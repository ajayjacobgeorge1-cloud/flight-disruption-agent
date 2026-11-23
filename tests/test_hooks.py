from app import state
from app.hooks import pre_tool_use_hook


def setup_function():
    state.reset_host_state()


def test_rebook_below_threshold_is_allowed():
    decision = pre_tool_use_hook(
        "rebook_itinerary", {"pnr": "ABC123", "new_flight_number": "AA125", "fare_difference_usd": 340}
    )
    assert decision.allow is True
    assert state.ESCALATION_TICKETS == []


def test_rebook_at_or_above_threshold_is_denied_and_escalated():
    decision = pre_tool_use_hook(
        "rebook_itinerary", {"pnr": "ABC123", "new_flight_number": "AA129", "fare_difference_usd": 1250}
    )
    assert decision.allow is False
    assert decision.replacement_result["errorCategory"] == "permission"
    assert decision.replacement_result["isRetryable"] is False
    assert len(state.ESCALATION_TICKETS) == 1
    ticket = state.ESCALATION_TICKETS[0]
    assert ticket["pnr"] == "ABC123"
    assert ticket["fare_difference_usd"] == 1250


def test_non_rebooking_tools_are_always_allowed():
    decision = pre_tool_use_hook("get_booking", {"pnr": "ABC123"})
    assert decision.allow is True
