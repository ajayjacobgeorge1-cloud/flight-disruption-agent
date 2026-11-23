"""Host-side (orchestrator) state -- as opposed to the mock booking backend,
which now lives behind the MCP server and has its own process memory.

The PreToolUse hook runs in the orchestrator, before a tool call ever
reaches the MCP server, so the escalation queue it writes to belongs here,
not in the backend.
"""
from __future__ import annotations

ESCALATION_TICKETS: list[dict] = []


def reset_host_state() -> None:
    ESCALATION_TICKETS.clear()
