"""CLI entry point for the flight-disruption coordinator agent.

Usage:
    python main.py "My PNR is ABC123, my flight was cancelled -- I need
    rebooking options and want to know about hotel compensation."
"""
from __future__ import annotations

import json
import sys

from app.coordinator import run_coordinator
from app.state import ESCALATION_TICKETS


def main() -> None:
    if len(sys.argv) > 1:
        user_message = " ".join(sys.argv[1:])
    else:
        user_message = input("Customer message: ")

    result = run_coordinator(user_message)

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
