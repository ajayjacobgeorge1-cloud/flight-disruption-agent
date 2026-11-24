"""Coordinator agent: decomposes a (possibly multi-concern) customer
message, dispatches independent subagents via a `Task` tool (in parallel
when the model issues several Task calls in one turn), and synthesizes a
single response. The sensitive `rebook_itinerary` tool is available
directly to the coordinator and is gated by the PreToolUse hook.
"""
from __future__ import annotations

import json
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


def run_coordinator(
    user_message: str,
    client: Optional[LLMClient] = None,
    subagent_client: Optional[LLMClient] = None,
    max_iterations: Optional[int] = None,
    system_prompt_override: Optional[str] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> CoordinatorRunResult:
    client = client or LLMClient()
    subagent_client = subagent_client or client
    max_iterations = max_iterations or SETTINGS.max_agent_loop_iterations
    system_prompt = system_prompt_override or _load_prompt(COORDINATOR_PROMPT_FILE)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    subagent_calls: list[SubagentCallRecord] = []
    tool_calls: list[ToolCallRecord] = []
    nudges_used = 0

    for _ in range(max_iterations):
        response = client.chat(messages, tools=COORDINATOR_TOOLS, on_token=on_token)

        # See app/agent_loop.py's matching check: "answered without ever
        # dispatching a subagent or calling a tool" is a reliable failure
        # signal on its own, independent of what the text looks like.
        no_action_taken = not subagent_calls and not tool_calls
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

    return CoordinatorRunResult(
        final_text="(stopped: exceeded max agent-loop iterations without reaching end_turn)",
        messages=messages,
        subagent_calls=subagent_calls,
        tool_calls=tool_calls,
        hit_iteration_cap=True,
    )
