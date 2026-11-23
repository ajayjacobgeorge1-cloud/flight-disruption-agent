"""MCP server exposing the disruption agent's tools over the Model Context
Protocol (stdio transport).

The coordinator and every subagent never call these functions directly --
they go through app/mcp_client.py, which launches this file as a child
process (`python -m mcp_server.server`) and talks to it over MCP, exactly
as a real external tool provider would be integrated.

Run standalone for manual inspection (it will just wait on stdio, Ctrl+C
to quit):
    python -m mcp_server.server
"""
from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from app.errors import ToolError
from app.mock_data import reset_mock_state as _reset_mock_state_impl
from mcp_server import tool_impl

mcp = FastMCP("flight-disruption-tools")


def _as_result(fn, **kwargs) -> dict:
    try:
        return fn(**kwargs)
    except ToolError as e:
        e.tool_name = fn.__name__
        return e.to_dict()


@mcp.tool(
    description=(
        "Retrieve a passenger's RESERVATION RECORD by PNR: passenger name, fare class, "
        "ticket status, and the booked itinerary segments (flight numbers, dates, "
        "origin/destination) exactly as ticketed. Use this FIRST whenever a PNR is "
        "mentioned, to find out what the passenger is actually booked on. "
        "Do NOT use this to check whether a flight is currently delayed or cancelled -- "
        "it returns the booking as ticketed, not real-time operational status. For live "
        "status, call get_flight_status with the flight_number and departure_date found "
        "here."
    )
)
def get_booking(pnr: str) -> dict:
    """pnr: 6-character booking reference, e.g. 'ABC123'."""
    return _as_result(tool_impl.get_booking, pnr=pnr)


@mcp.tool(
    description=(
        "Check the REAL-TIME OPERATIONAL STATUS of ONE specific, already-scheduled "
        "flight (on_time / delayed / cancelled), its disruption reason, gate, and "
        "estimated departure. Requires an exact flight_number and date -- normally the "
        "ones returned by get_booking for the passenger's own flight. "
        "Do NOT use this to find other flights the passenger could take instead -- it "
        "only reports on the single flight number you pass in, it does not search a "
        "schedule. For rebooking candidates, use search_alternatives instead."
    )
)
def get_flight_status(flight_number: str, date: str) -> dict:
    """flight_number: e.g. 'AA123'. date: YYYY-MM-DD departure date."""
    return _as_result(tool_impl.get_flight_status, flight_number=flight_number, date=date)


@mcp.tool(
    description=(
        "SEARCH for alternative, bookable flights between an origin and destination on "
        "a given date -- used to find rebooking options after a disruption. Returns a "
        "list of candidate flights with seat availability and fare difference versus the "
        "original ticket. This does NOT check the operational status of any specific "
        "flight (use get_flight_status for that) and it does NOT book anything (use "
        "rebook_itinerary for that) -- it only lists candidates to choose from."
    )
)
def search_alternatives(
    origin: str, destination: str, date: str, cabin_class: Optional[str] = None
) -> dict:
    """origin/destination: 3-letter IATA codes. date: YYYY-MM-DD. cabin_class: optional
    preferred cabin (Economy/Premium Economy/Business); omit to return all cabins."""
    return _as_result(
        tool_impl.search_alternatives,
        origin=origin,
        destination=destination,
        date=date,
        cabin_class=cabin_class,
    )


@mcp.tool(
    description=(
        "Look up the airline's IRROPS (irregular operations) policy on a topic -- one of "
        "'rebooking', 'hotel', 'compensation', or 'escalation' -- and return the relevant "
        "policy clause with a source citation. Use this to ground any statement about "
        "what the passenger is entitled to; never state entitlements from memory. This "
        "tool only reads policy text; it has no knowledge of this passenger's specific "
        "booking or flight."
    )
)
def read_policy(topic: str) -> dict:
    """topic: one of 'rebooking', 'hotel', 'compensation', 'escalation'."""
    return _as_result(tool_impl.read_policy, topic=topic)


@mcp.tool(
    description=(
        "MUTATING ACTION: actually rebook the passenger's PNR onto a new flight_number "
        "chosen from search_alternatives, at the given fare_difference_usd. This changes "
        "the live reservation -- only call it once a specific alternative has been "
        "selected and the fare difference is known. The calling agent/orchestrator is "
        "expected to enforce its own approval threshold before calling this -- this tool "
        "performs no business-rule check of its own."
    )
)
def rebook_itinerary(pnr: str, new_flight_number: str, fare_difference_usd: float) -> dict:
    """pnr: booking reference. new_flight_number: the chosen alternative's flight number.
    fare_difference_usd: fare delta versus the original ticket, from search_alternatives."""
    return _as_result(
        tool_impl.rebook_itinerary,
        pnr=pnr,
        new_flight_number=new_flight_number,
        fare_difference_usd=fare_difference_usd,
    )


@mcp.tool(
    description=(
        "TEST/EVAL-ONLY: resets the mock booking backend (rebooked segments, transient-"
        "failure counters) to its initial state. Not meant to be called by the "
        "coordinator or any subagent during normal operation."
    )
)
def reset_mock_state() -> dict:
    _reset_mock_state_impl()
    return {"reset": True}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
