"""Thin client over an OpenAI-compatible /v1/chat/completions endpoint.

The platform endpoint speaks the OpenAI chat-completions wire format
(messages / tools / tool_calls / finish_reason) in front of Gemini. The
exercise spec talks in Anthropic Messages API terms ("stop_reason":
"tool_use" | "end_turn"), so this client normalizes finish_reason into
that vocabulary and gives every other module a single place to depend on.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import requests

from app.config import SETTINGS

FINISH_REASON_TO_STOP_REASON = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "refusal",
}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    stop_reason: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """Calls the chat-completions endpoint. Swap in a fake via `transport`
    for offline tests / eval dry-runs without a live API key."""

    def __init__(
        self,
        model: Optional[str] = None,
        transport: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    ):
        self.model = model or SETTINGS.llm_model
        self._transport = transport or self._http_transport

    def _http_transport(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {SETTINGS.llm_api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            SETTINGS.llm_api_base_url,
            headers=headers,
            json=payload,
            timeout=SETTINGS.request_timeout_s,
        )
        resp.raise_for_status()
        return resp.json()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        raw = self._transport(payload)
        return self._parse(raw)

    @staticmethod
    def _parse(raw: dict[str, Any]) -> LLMResponse:
        choice = raw["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")
        stop_reason = FINISH_REASON_TO_STOP_REASON.get(finish_reason, finish_reason)

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))

        if tool_calls and stop_reason != "tool_use":
            stop_reason = "tool_use"

        return LLMResponse(
            stop_reason=stop_reason,
            content=message.get("content") or "",
            tool_calls=tool_calls,
            raw=raw,
        )
