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
        "Get the passenger's reservation for a PNR: name, fare class, ticket status, "
        "and booked flight segments as ticketed. Not live flight status -- use "
        "get_flight_status for that."
    )
)
def get_booking(pnr: str) -> dict:
    """pnr: 6-character booking reference, e.g. 'ABC123'."""
    return _as_result(tool_impl.get_booking, pnr=pnr)


@mcp.tool(
    description=(
        "Get the real-time status (on_time/delayed/cancelled) of one specific "
        "flight_number and date. Not for finding other flights -- use "
        "search_alternatives for that."
    )
)
def get_flight_status(flight_number: str, date: str) -> dict:
    """flight_number: e.g. 'AA123'. date: YYYY-MM-DD departure date."""
    return _as_result(tool_impl.get_flight_status, flight_number=flight_number, date=date)


@mcp.tool(
    description=(
        "Search bookable alternative flights between an origin and destination on a "
        "date, with fares. Does not check a specific flight's status (use "
        "get_flight_status) and does not book anything (use rebook_itinerary)."
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
        "Look up IRROPS policy text for a topic (rebooking/hotel/compensation/"
        "escalation), with a citation. Ground any entitlement claim in this -- never "
        "state one from memory. Has no booking or flight data."
    )
)
def read_policy(topic: str) -> dict:
    """topic: one of 'rebooking', 'hotel', 'compensation', 'escalation'."""
    return _as_result(tool_impl.read_policy, topic=topic)


@mcp.tool(
    description=(
        "Rebook a PNR onto new_flight_number at fare_difference_usd. Mutates the live "
        "reservation -- call only once a specific alternative and fare are known. The "
        "caller must enforce its own approval threshold; this tool does not."
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
