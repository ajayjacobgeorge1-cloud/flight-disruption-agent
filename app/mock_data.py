"""In-memory mock backend data. Stands in for a GDS / PSS integration."""
from __future__ import annotations

BOOKINGS = {
    "ABC123": {
        "pnr": "ABC123",
        "passenger_name": "Ajay George",
        "fare_class": "Economy Flex",
        "ticket_status": "confirmed",
        "segments": [
            {
                "flight_number": "AA123",
                "origin": "JFK",
                "destination": "LHR",
                "departure_date": "2026-11-24",
                "departure_time": "21:10",
            }
        ],
    },
    "XYZ789": {
        "pnr": "XYZ789",
        "passenger_name": "Priya Nair",
        "fare_class": "Economy Basic",
        "ticket_status": "confirmed",
        "segments": [
            {
                "flight_number": "AA456",
                "origin": "ORD",
                "destination": "SFO",
                "departure_date": "2026-11-25",
                "departure_time": "08:40",
            }
        ],
    },
}

FLIGHT_STATUS = {
    ("AA123", "2026-11-24"): {
        "flight_number": "AA123",
        "date": "2026-11-24",
        "status": "cancelled",
        "reason": "mechanical",
        "gate": None,
        "estimated_departure": None,
    },
    ("AA456", "2026-11-25"): {
        "flight_number": "AA456",
        "date": "2026-11-25",
        "status": "delayed",
        "reason": "weather",
        "gate": "B14",
        "estimated_departure": "11:55",
    },
    # Simulates a transient upstream timeout the first time this key is queried.
    ("AA999", "2026-11-26"): {
        "flight_number": "AA999",
        "date": "2026-11-26",
        "status": "on_time",
        "reason": None,
        "gate": "C2",
        "estimated_departure": "14:00",
    },
}

ALTERNATIVES = {
    ("JFK", "LHR", "2026-11-24"): [
        {
            "flight_number": "AA125",
            "departure_time": "23:55",
            "arrival_time_next_day": "11:40",
            "cabin_class": "Economy",
            "seats_available": 4,
            "fare_difference_usd": 0,
        },
        {
            "flight_number": "BA178",
            "departure_time": "22:30",
            "arrival_time_next_day": "10:15",
            "cabin_class": "Premium Economy",
            "seats_available": 2,
            "fare_difference_usd": 340,
        },
        {
            "flight_number": "AA129",
            "departure_time": "07:15",
            "arrival_time_next_day": None,
            "cabin_class": "Business",
            "seats_available": 1,
            "fare_difference_usd": 1250,
        },
    ],
    ("ORD", "SFO", "2026-11-25"): [
        {
            "flight_number": "AA460",
            "departure_time": "13:05",
            "arrival_time_next_day": None,
            "cabin_class": "Economy",
            "seats_available": 6,
            "fare_difference_usd": 0,
        },
    ],
}

# Simple call counter to simulate a transient error that succeeds on retry.
_TRANSIENT_CALL_COUNTS: dict[str, int] = {}


def note_call_and_should_fail_transiently(key: str) -> bool:
    """First call for a given key fails transiently; subsequent calls succeed."""
    count = _TRANSIENT_CALL_COUNTS.get(key, 0)
    _TRANSIENT_CALL_COUNTS[key] = count + 1
    return count == 0


def reset_mock_state() -> None:
    """Resets this backend's mutable state (rebooked segments, transient-
    failure counters) between eval/test runs. Runs wherever this module is
    imported -- inside the MCP server subprocess when called via its
    'reset_mock_state' tool, or in-process when tool_impl is imported
    directly by tests. Escalation tickets are tracked separately, host-side,
    in app.state -- see app.state.reset_host_state()."""
    _TRANSIENT_CALL_COUNTS.clear()
    BOOKINGS["ABC123"]["segments"][0]["flight_number"] = "AA123"
    BOOKINGS["XYZ789"]["segments"][0]["flight_number"] = "AA456"
