"""CLI entry point for the flight-disruption coordinator agent.

Usage:
    python main.py "My PNR is ABC123, my flight was cancelled -- I need
    rebooking options and want to know about hotel compensation."
"""
from __future__ import annotations

import json
import sys
import traceback

from app.coordinator import run_coordinator
from app.state import ESCALATION_TICKETS


def main() -> None:
    if len(sys.argv) > 1:
        user_message = " ".join(sys.argv[1:])
    else:
        user_message = input("Customer message: ")

    try:
        result = run_coordinator(user_message)
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in (403, 429):
            print(
                "\nWe're getting too many requests at the moment. Please "
                "wait a little while and try again."
            )
        else:
            print(
                "\nSomething went wrong while processing your request (a "
                "backend/network error). Please try again in a moment."
            )
        print("\n--- technical detail ---", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    print("\n=== Subagent dispatch trace ===")
    for call in result.subagent_calls:
        print(f"- {call.subagent_type} ({'FAILED' if call.failed else 'ok'}):")
        for tc in call.tool_calls:
            tag = " [DENIED BY HOOK]" if tc.denied_by_hook else ""
            print(f"    tool={tc.name} args={tc.arguments}{tag}")
        print(f"    findings: {call.findings[:300]}")

    if result.tool_calls:
        print("\n=== Coordinator-level tool calls ===")
        for tc in result.tool_calls:
            tag = " [DENIED BY HOOK]" if tc.denied_by_hook else ""
            print(f"- {tc.name}{tag}: {json.dumps(tc.result)}")

    if ESCALATION_TICKETS:
        print("\n=== Escalation tickets created ===")
        for t in ESCALATION_TICKETS:
            print(f"- {t}")

    print("\n=== Final response to customer ===")
    print(result.final_text)


if __name__ == "__main__":
    main()
