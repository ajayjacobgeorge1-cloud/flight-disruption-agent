"""Business logic for the disruption agent's tools.

This is the actual implementation backing the MCP server (mcp_server/server.py).
Kept as plain, import-light functions (no MCP types here) so tests can call
them directly without spinning up a subprocess, and so the MCP server stays
a thin transport wrapper around this module.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app import mock_data
from app.errors import transient, validation

POLICY_DOC_PATH = Path(__file__).resolve().parent.parent / "app" / "policy_docs" / "irrops_policy.md"

_PNR_RE = re.compile(r"^[A-Z0-9]{5,8}$")


def get_booking(pnr: str) -> dict[str, Any]:
    pnr = (pnr or "").strip().upper()
    if not _PNR_RE.match(pnr):
        raise validation(f"'{pnr}' is not a valid PNR format (5-8 alphanumeric characters).", pnr=pnr)
    booking = mock_data.BOOKINGS.get(pnr)
    if booking is None:
        raise validation(f"No booking found for PNR '{pnr}'.", pnr=pnr)
    return booking


def get_flight_status(flight_number: str, date: str) -> dict[str, Any]:
    key = (flight_number, date)
    cache_key = f"status::{flight_number}::{date}"
    if mock_data.note_call_and_should_fail_transiently(cache_key):
        raise transient(
            f"Upstream flight-status feed timed out while looking up {flight_number} on {date}. Retry.",
            flight_number=flight_number,
            date=date,
        )
    status = mock_data.FLIGHT_STATUS.get(key)
    if status is None:
        raise validation(f"No status on file for flight {flight_number} on {date}.", flight_number=flight_number, date=date)
    return status


def search_alternatives(origin: str, destination: str, date: str, cabin_class: str | None = None) -> dict[str, Any]:
    origin, destination = (origin or "").strip().upper(), (destination or "").strip().upper()
    if len(origin) != 3 or len(destination) != 3:
        raise validation("origin and destination must be 3-letter IATA codes.", origin=origin, destination=destination)
    candidates = mock_data.ALTERNATIVES.get((origin, destination, date), [])
    if cabin_class:
        candidates = [c for c in candidates if c["cabin_class"].lower() == cabin_class.lower()]
    return {"origin": origin, "destination": destination, "date": date, "candidates": candidates}


def read_policy(topic: str) -> dict[str, Any]:
    valid_topics = {"rebooking", "hotel", "compensation", "escalation"}
    if topic not in valid_topics:
        raise validation(f"Unknown policy topic '{topic}'. Valid topics: {sorted(valid_topics)}.", topic=topic)

    text = POLICY_DOC_PATH.read_text(encoding="utf-8")
    match = re.search(rf"## {topic}\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not match:
        raise transient(f"Policy section '{topic}' exists but could not be parsed from the doc store.", topic=topic)
    return {
        "topic": topic,
        "clause": match.group(1).strip(),
        "source": "irrops_policy.md v3.2",
    }


def rebook_itinerary(pnr: str, new_flight_number: str, fare_difference_usd: float) -> dict[str, Any]:
    pnr = (pnr or "").strip().upper()
    if pnr not in mock_data.BOOKINGS:
        raise validation(f"Cannot rebook: no booking found for PNR '{pnr}'.", pnr=pnr)
    if fare_difference_usd < 0:
        raise validation("fare_difference_usd cannot be negative.", fare_difference_usd=fare_difference_usd)

    booking = mock_data.BOOKINGS[pnr]
    booking["segments"][0]["flight_number"] = new_flight_number
    return {
        "pnr": pnr,
        "rebooked_to": new_flight_number,
        "fare_difference_usd": fare_difference_usd,
        "status": "confirmed",
    }
