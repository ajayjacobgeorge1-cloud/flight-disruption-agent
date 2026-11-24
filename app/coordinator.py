"""Coordinator agent: decomposes a (possibly multi-concern) customer
message, dispatches independent subagents via a `Task` tool (in parallel
when the model issues several Task calls in one turn), and synthesizes a
single response. The sensitive `rebook_itinerary` tool is available
directly to the coordinator and is gated by the PreToolUse hook.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.agent_loop import (
    MAX_SELF_CORRECTION_NUDGES,
    NO_ACTION_NUDGE,
    ToolCallRecord,
    looks_like_unfulfilled_plan,
)
from app.config import SETTINGS
from app.hooks import pre_tool_use_hook
from app.llm_client import LLMClient
from app.subagents import run_subagent
from app.tools import REBOOK_ITINERARY_SCHEMA, execute_tool

PROMPTS_DIR = Path(__file__).parent / "prompts"
COORDINATOR_PROMPT_FILE = "coordinator_system.md"

TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "Task",
        "description": (
            "Delegate one unit of work to a named subagent: 'status-analyst', "
            "'options-finder', or 'policy-checker'. Give it a complete, "
            "self-contained prompt -- the subagent sees only what you write "
            "here, not the rest of this conversation. Call this tool several "
            "times in the SAME turn to run independent subagents in parallel "
            "(e.g. options-finder and policy-checker once status-analyst's "
            "findings are known)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subagent_type": {
                    "type": "string",
                    "enum": ["status-analyst", "options-finder", "policy-checker"],
                },
                "prompt": {
                    "type": "string",
                    "description": "Full task + any findings the subagent needs as context.",
                },
            },
            "required": ["subagent_type", "prompt"],
        },
    },
}

COORDINATOR_TOOLS = [TASK_SCHEMA, REBOOK_ITINERARY_SCHEMA]

# Injected once per tool round, after real tool/subagent results are
# already in `messages` -- deliberately kept OUT of the system prompt
# (see coordinator_system.md), which only ever asks the model to dispatch
# or answer directly. Showing the full "synthesize a unified response"
# checklist only now, grounded by the real findings immediately above it,
# is what keeps a small model from jumping straight to a fabricated
# synthesis on turn one, before anything has actually been looked up.
SYNTHESIS_STEERING_MESSAGE = (
    "Decide what to do next using ONLY the verified findings above:\n"
    "- If every concern the customer raised is now covered, write ONE "
    "final customer-facing response. Ground every fact in the findings "
    "above -- never invent a flight number, name, time, or policy detail "
    "that isn't there. Cite which subagent/tool each fact came from, flag "
    "any conflict between findings instead of silently picking one, and "
    "note any concern you still could not resolve.\n"
    "- Otherwise, call Task now for exactly the subagent(s) you still "
    "need -- do not write a customer-facing answer yet."
)


@dataclass
class SubagentCallRecord:
    subagent_type: str
    prompt: str
    findings: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    failed: bool = False


@dataclass
class CoordinatorRunResult:
    final_text: str
    messages: list[dict[str, Any]]
    subagent_calls: list[SubagentCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    hit_iteration_cap: bool = False


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# A PNR-shaped token: 5-7 alphanumeric characters mixing at least one
# letter and one digit (plain English words essentially never do this).
_PNR_LIKE_RE = re.compile(
    r"\b(?=[A-Za-z0-9]{5,7}\b)(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{5,7}\b"
)
_LOOKUP_KEYWORDS = (
    "pnr", "flight", "cancel", "delay", "disrupt", "rebook", "hotel",
    "compensat", "refund", "status", "booking", "itinerary", "reservation",
)


def message_plausibly_needs_lookup(user_message: str) -> bool:
    """Does this message plausibly require a subagent/tool lookup, or is
    it small talk ("hi", "thanks") the model should just answer directly?
    Scopes the "answered without taking any action" self-correction check
    below -- without this, a perfectly fine "Hi, how can I help?" would
    get needlessly retried just because no tool was called for it."""
    lowered = user_message.lower()
    if any(kw in lowered for kw in _LOOKUP_KEYWORDS):
        return True
    return bool(_PNR_LIKE_RE.search(user_message))


# Exact-match small talk handled deterministically, without spending an
# LLM call: tested live, even a bare "Hi" got the model inventing a whole
# disruption scenario (a narrated plan, a "[insert PNR here]" placeholder)
# instead of just greeting back -- the same unreliability we've seen on
# real requests, just surfacing on trivial ones too. Rather than hope a
# small model handles small talk well, don't ask it to.
_GREETING_PHRASES = {
    "hi", "hello", "hey", "hiya", "yo", "hi there", "hello there",
    "good morning", "good afternoon", "good evening", "sup",
    "what's up", "whats up",
}
_CLOSING_PHRASES = {
    "thanks", "thank you", "thanks a lot", "ok", "okay", "bye",
    "goodbye", "cool", "great", "sounds good", "got it",
}
GREETING_RESPONSE = (
    "Hello! I'm the flight-disruption support assistant. How can I "
    "assist you today? Share your PNR and what's going on (a "
    "cancellation, delay, rebooking need, or a question about "
    "hotel/compensation policy) and I'll look into it."
)
CLOSING_RESPONSE = "You're welcome! Let me know if there's anything else I can help with."


def canned_small_talk_response(user_message: str) -> Optional[str]:
    normalized = user_message.strip().lower().rstrip("!.?,")
    if normalized in _GREETING_PHRASES:
        return GREETING_RESPONSE
    if normalized in _CLOSING_PHRASES:
        return CLOSING_RESPONSE
    return None


def run_coordinator(
    user_message: str,
    client: Optional[LLMClient] = None,
    subagent_client: Optional[LLMClient] = None,
    max_iterations: Optional[int] = None,
    system_prompt_override: Optional[str] = None,
    on_token: Optional[Callable[[str], None]] = None,
    conversation_history: Optional[list[dict[str, str]]] = None,
) -> CoordinatorRunResult:
    """`conversation_history` is prior turns of the CUSTOMER-FACING
    conversation only (plain {"role": "user"|"assistant", "content": ...}
    entries, as a caller like the Streamlit app would already have for
    display) -- never the internal Task/tool scaffolding from previous
    turns, which would bloat context and replay old nudge messages for no
    benefit. It's inserted between the system prompt and this turn's
    message so the model has memory of what was already asked/answered."""
    client = client or LLMClient()
    subagent_client = subagent_client or client
    max_iterations = max_iterations or SETTINGS.max_agent_loop_iterations
    system_prompt = system_prompt_override or _load_prompt(COORDINATOR_PROMPT_FILE)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    canned = canned_small_talk_response(user_message)
    if canned is not None:
        messages.append({"role": "assistant", "content": canned})
        return CoordinatorRunResult(final_text=canned, messages=messages)

    subagent_calls: list[SubagentCallRecord] = []
    tool_calls: list[ToolCallRecord] = []
    nudges_used = 0
    needs_lookup = message_plausibly_needs_lookup(user_message)

    for _ in range(max_iterations):
        response = client.chat(messages, tools=COORDINATOR_TOOLS, on_token=on_token)

        # See app/agent_loop.py's matching check: "answered without ever
        # dispatching a subagent or calling a tool" is a reliable failure
        # signal -- but only when the message plausibly needed one; a
        # plain "Hi, how can I help?" to a greeting is not a failure just
        # because no tool was called.
        no_action_taken = not subagent_calls and not tool_calls and needs_lookup
        if response.stop_reason != "tool_use" and (
            looks_like_unfulfilled_plan(response.content) or no_action_taken
        ):
            if nudges_used < MAX_SELF_CORRECTION_NUDGES:
                nudges_used += 1
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": NO_ACTION_NUDGE})
                continue

        if response.stop_reason != "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            return CoordinatorRunResult(
                final_text=response.content,
                messages=messages,
                subagent_calls=subagent_calls,
                tool_calls=tool_calls,
            )

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": response.content or None}
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in response.tool_calls
        ]
        messages.append(assistant_msg)

        task_calls = [tc for tc in response.tool_calls if tc.name == "Task"]
        other_calls = [tc for tc in response.tool_calls if tc.name != "Task"]
        results_by_id: dict[str, dict[str, Any]] = {}

        if task_calls:
            with ThreadPoolExecutor(max_workers=max(1, len(task_calls))) as pool:
                futures = {
                    pool.submit(
                        run_subagent, tc.arguments.get("subagent_type", ""), tc.arguments.get("prompt", ""), subagent_client
                    ): tc
                    for tc in task_calls
                }
                for fut, tc in futures.items():
                    subagent_type = tc.arguments.get("subagent_type", "")
                    prompt = tc.arguments.get("prompt", "")
                    try:
                        run_result = fut.result()
                        record = SubagentCallRecord(
                            subagent_type=subagent_type,
                            prompt=prompt,
                            findings=run_result.final_text,
                            tool_calls=run_result.tool_calls,
                        )
                        payload = {"subagent_type": subagent_type, "findings": run_result.final_text}
                    except Exception as exc:  # subagent crashed outright
                        record = SubagentCallRecord(
                            subagent_type=subagent_type, prompt=prompt, findings=str(exc), failed=True
                        )
                        payload = {
                            "error": True,
                            "errorCategory": "transient",
                            "isRetryable": True,
                            "message": f"Subagent '{subagent_type}' failed: {exc}",
                        }
                    subagent_calls.append(record)
                    results_by_id[tc.id] = payload

        for tc in other_calls:
            decision = pre_tool_use_hook(tc.name, tc.arguments)
            if not decision.allow:
                payload = decision.replacement_result
                denied = True
            else:
                payload = execute_tool(tc.name, tc.arguments)
                denied = False
            tool_calls.append(ToolCallRecord(tc.name, tc.arguments, payload, denied_by_hook=denied))
            results_by_id[tc.id] = payload

        for tc in response.tool_calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": json.dumps(results_by_id[tc.id]),
                }
            )

        # Mistral's chat template requires a "tool" message be followed by
        # an "assistant" turn, not an injected "user" turn (confirmed: that
        # shape gets a bare 400 from the endpoint) -- so the steering
        # instruction rides along on the last tool result's own content
        # instead of as a separate message.
        messages[-1]["content"] += "\n\n" + SYNTHESIS_STEERING_MESSAGE

    return CoordinatorRunResult(
        final_text="(stopped: exceeded max agent-loop iterations without reaching end_turn)",
        messages=messages,
        subagent_calls=subagent_calls,
        tool_calls=tool_calls,
        hit_iteration_cap=True,
    )
