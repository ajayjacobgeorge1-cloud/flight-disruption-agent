"""Thin client over an OpenAI-compatible /v1/chat/completions endpoint.

The platform endpoint speaks the OpenAI chat-completions wire format
(messages / tools / tool_calls / finish_reason) in front of Gemini. The
exercise spec talks in Anthropic Messages API terms ("stop_reason":
"tool_use" | "end_turn"), so this client normalizes finish_reason into
that vocabulary and gives every other module a single place to depend on.
"""
from __future__ import annotations

import json
import threading
import time
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

# The platform gateway occasionally returns one of these for a healthy
# backend (observed: a bare 403 that clears up on the very next request)
# -- worth a couple of quick retries rather than failing the whole
# coordinator/subagent run over a one-off gateway blip.
_TRANSIENT_HTTP_STATUS = {403, 429, 502, 503, 504}
_MAX_HTTP_RETRIES = 3
_RETRY_BACKOFF_S = 2.0

# The coordinator dispatches subagents concurrently (app/coordinator.py's
# ThreadPoolExecutor) -- that's a logical/architectural parallelism, not a
# promise that the gateway can take several simultaneous requests. Observed:
# 3 subagents firing at once (each possibly retrying) reliably triggers a
# sustained 403 that even several retries-with-backoff don't clear, whereas
# a lone request right next to it succeeds immediately. So the actual HTTP
# calls are serialized here, process-wide, independent of how many threads
# are logically "in flight" above this module.
_OUTBOUND_REQUEST_SEMAPHORE = threading.Semaphore(1)


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

    @staticmethod
    def _post_with_retries(headers: dict[str, str], payload: dict[str, Any], stream: bool) -> requests.Response:
        with _OUTBOUND_REQUEST_SEMAPHORE:
            for attempt in range(_MAX_HTTP_RETRIES + 1):
                resp = requests.post(
                    SETTINGS.llm_api_base_url,
                    headers=headers,
                    json=payload,
                    timeout=SETTINGS.request_timeout_s,
                    stream=stream,
                )
                if resp.status_code not in _TRANSIENT_HTTP_STATUS or attempt == _MAX_HTTP_RETRIES:
                    resp.raise_for_status()
                    return resp
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
        raise AssertionError("unreachable")  # loop always returns or raises

    def _http_transport(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "x-bf-vk": SETTINGS.llm_api_key,
            "Content-Type": "application/json",
        }
        resp = self._post_with_retries(headers, payload, stream=False)
        return resp.json()

    def _http_transport_stream(
        self, payload: dict[str, Any], on_token: Callable[[str], None]
    ) -> dict[str, Any]:
        """Same endpoint, SSE (`stream: true`). Calls `on_token` with each
        incremental content chunk as it arrives, and reassembles the deltas
        into the same raw shape `_http_transport` would have returned, so
        `_parse` stays the single source of truth for both paths."""
        headers = {
            "x-bf-vk": SETTINGS.llm_api_key,
            "Content-Type": "application/json",
        }
        resp = self._post_with_retries(headers, {**payload, "stream": True}, stream=True)

        content_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, str]] = {}
        finish_reason = "stop"

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            choice = chunk["choices"][0]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta", {})
            if delta.get("content"):
                content_parts.append(delta["content"])
                on_token(delta["content"])
            for tc_delta in delta.get("tool_calls") or []:
                idx = tc_delta.get("index", 0)
                slot = tool_call_parts.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc_delta.get("id"):
                    slot["id"] = tc_delta["id"]
                fn = tc_delta.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

        message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts) or None}
        if tool_call_parts:
            message["tool_calls"] = [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
                for _, slot in sorted(tool_call_parts.items())
            ]
        return {"choices": [{"finish_reason": finish_reason, "message": message}]}

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.0,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if on_token is not None and self._transport is self._http_transport:
            raw = self._http_transport_stream(payload, on_token)
        else:
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
