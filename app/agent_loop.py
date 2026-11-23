"""Generic agentic loop: checks stop_reason to decide whether to keep
executing tools or to present the final response.

Shared by the coordinator and every subagent -- only the system prompt,
tool schemas, and available tool names differ per role.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.config import SETTINGS
from app.hooks import pre_tool_use_hook
from app.llm_client import LLMClient, LLMResponse
from app.tools import execute_tool

ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    denied_by_hook: bool = False


@dataclass
class AgentRunResult:
    final_text: str
    messages: list[dict[str, Any]]
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    hit_iteration_cap: bool = False


def _default_tool_executor(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    decision = pre_tool_use_hook(name, arguments)
    if not decision.allow:
        return decision.replacement_result or {
            "error": True,
            "errorCategory": "permission",
            "isRetryable": False,
            "message": decision.reason or "Denied by policy hook.",
        }
    return execute_tool(name, arguments)


def run_agent_loop(
    system_prompt: str,
    user_message: str,
    tool_schemas: list[dict[str, Any]],
    client: Optional[LLMClient] = None,
    tool_executor: ToolExecutor = _default_tool_executor,
    max_iterations: Optional[int] = None,
) -> AgentRunResult:
    client = client or LLMClient()
    max_iterations = max_iterations or SETTINGS.max_agent_loop_iterations

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    tool_calls: list[ToolCallRecord] = []

    for _ in range(max_iterations):
        response: LLMResponse = client.chat(messages, tools=tool_schemas)

        if response.stop_reason == "tool_use":
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

            for tc in response.tool_calls:
                decision = pre_tool_use_hook(tc.name, tc.arguments)
                if not decision.allow:
                    result = decision.replacement_result
                    denied = True
                else:
                    result = execute_tool(tc.name, tc.arguments)
                    denied = False

                tool_calls.append(ToolCallRecord(tc.name, tc.arguments, result, denied_by_hook=denied))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": json.dumps(result),
                    }
                )
            continue

        # end_turn (or max_tokens/refusal, which we also treat as final)
        messages.append({"role": "assistant", "content": response.content})
        return AgentRunResult(final_text=response.content, messages=messages, tool_calls=tool_calls)

    return AgentRunResult(
        final_text="(stopped: exceeded max agent-loop iterations without reaching end_turn)",
        messages=messages,
        tool_calls=tool_calls,
        hit_iteration_cap=True,
    )
