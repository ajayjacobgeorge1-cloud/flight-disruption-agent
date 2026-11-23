"""Programmatic PreToolUse hook enforcing a business rule.

This runs BEFORE the tool implementation executes -- it has no access to an
LLM's judgment and cannot be reasoned around by prompting. If it denies the
call, the tool never runs; the agent gets a structured redirect to the
escalation workflow instead of a tool result.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from app.config import SETTINGS
from app.errors import permission
from app.state import ESCALATION_TICKETS


@dataclass
class HookDecision:
    allow: bool
    reason: Optional[str] = None
    replacement_result: Optional[dict[str, Any]] = None


def pre_tool_use_hook(tool_name: str, arguments: dict[str, Any]) -> HookDecision:
    if tool_name != "rebook_itinerary":
        return HookDecision(allow=True)

    fare_difference = float(arguments.get("fare_difference_usd", 0) or 0)
    threshold = SETTINGS.rebooking_autonomy_threshold_usd
    if fare_difference < threshold:
        return HookDecision(allow=True)

    ticket_id = f"ESC-{uuid.uuid4().hex[:8].upper()}"
    ticket = {
        "ticket_id": ticket_id,
        "reason": "fare_difference_above_autonomy_threshold",
        "pnr": arguments.get("pnr"),
        "requested_flight": arguments.get("new_flight_number"),
        "fare_difference_usd": fare_difference,
        "threshold_usd": threshold,
    }
    ESCALATION_TICKETS.append(ticket)

    err = permission(
        f"Rebooking denied by policy hook: fare difference ${fare_difference:,.0f} "
        f"is at or above the ${threshold:,.0f} autonomy threshold. Escalated to a "
        f"human agent as ticket {ticket_id}.",
        ticket_id=ticket_id,
        escalation_ticket=ticket,
    )
    err.tool_name = tool_name
    return HookDecision(allow=False, reason=err.message, replacement_result=err.to_dict())
