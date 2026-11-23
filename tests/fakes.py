"""Offline test doubles -- no network, no real API key required.

FakeTransport lets a test script exact OpenAI-format chat-completion
responses (tool_calls then a final message) so the agent loop and
coordinator can be exercised deterministically without hitting the
Gemini-backed endpoint.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable

from app.llm_client import LLMClient

_id_counter = itertools.count(1)


def tool_call_response(calls: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Builds a raw OpenAI-format response with one or more tool calls."""
    import json

    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{next(_id_counter)}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for name, args in calls
                    ],
                },
            }
        ]
    }


def final_response(text: str) -> dict[str, Any]:
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


def scripted_client(script: list[dict[str, Any]]) -> LLMClient:
    """An LLMClient whose transport returns each entry of `script` in order,
    one per .chat() call. Raises if called more times than scripted."""
    queue = list(script)

    def transport(_payload: dict[str, Any]) -> dict[str, Any]:
        if not queue:
            raise AssertionError("scripted_client called more times than the script provides for")
        return queue.pop(0)

    return LLMClient(model="fake-model", transport=transport)


def constant_client(responder: Callable[[dict[str, Any]], dict[str, Any]]) -> LLMClient:
    """An LLMClient whose transport is driven by an arbitrary callable, for
    scenarios where the response needs to depend on the incoming payload
    (e.g. a subagent client that always answers 'done' regardless of the
    prompt it's given)."""
    return LLMClient(model="fake-model", transport=responder)
