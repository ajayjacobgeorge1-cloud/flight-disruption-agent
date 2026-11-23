"""Synchronous facade over the (async) MCP client SDK.

The rest of this codebase (agent_loop, coordinator) is synchronous, so this
module runs one persistent asyncio event loop on a background thread,
launches mcp_server/server.py as a child process over stdio on first use,
and exposes blocking list_tool_schemas()/call_tool() methods that submit
coroutines onto that loop and wait for the result. One client is shared
process-wide (get_mcp_client()) so the subprocess is only started once.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONNECT_TIMEOUT_S = 30
_CALL_TIMEOUT_S = 30


class MCPToolClient:
    """One connection to the flight-disruption-tools MCP server."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mcp-client-loop")
        self._thread.start()
        self._session: Optional[ClientSession] = None
        self._stdio_cm = None
        self._session_cm = None
        self._run(self._connect(), timeout=_CONNECT_TIMEOUT_S)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro, timeout: float = _CALL_TIMEOUT_S):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    async def _connect(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=str(PROJECT_ROOT),
        )
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()

    def list_tool_schemas(self) -> list[dict[str, Any]]:
        """Returns every tool the MCP server exposes, converted from MCP's
        Tool shape into OpenAI function-calling format."""

        async def _list():
            result = await self._session.list_tools()
            return result.tools

        tools = self._run(_list())
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema,
                },
            }
            for t in tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Calls a tool over MCP and returns its JSON result as a dict.
        Never raises for a tool-level failure -- MCP transport/protocol
        errors are converted into the same structured-error shape the rest
        of the app expects from a tool call."""

        async def _call():
            return await self._session.call_tool(name, {k: v for k, v in arguments.items() if v is not None})

        try:
            result = self._run(_call())
        except Exception as exc:
            return {
                "error": True,
                "errorCategory": "transient",
                "isRetryable": True,
                "message": f"MCP transport error calling '{name}': {exc}",
            }

        text_parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
        if result.isError:
            message = "\n".join(text_parts) or f"Tool '{name}' reported an error with no details."
            return {"error": True, "errorCategory": "transient", "isRetryable": True, "message": message}
        if not text_parts:
            return {}
        try:
            return json.loads(text_parts[0])
        except json.JSONDecodeError:
            return {"text": text_parts[0]}

    def close(self) -> None:
        async def _close():
            if self._session_cm is not None:
                await self._session_cm.__aexit__(None, None, None)
            if self._stdio_cm is not None:
                await self._stdio_cm.__aexit__(None, None, None)

        try:
            self._run(_close(), timeout=10)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


_client: Optional[MCPToolClient] = None
_client_lock = threading.Lock()


def get_mcp_client() -> MCPToolClient:
    """Process-wide singleton: the first call pays subprocess-startup cost,
    every later call (and every test in the same process) reuses it."""
    global _client
    with _client_lock:
        if _client is None:
            _client = MCPToolClient()
            atexit.register(_client.close)
        return _client


def reset_mcp_client_for_tests() -> None:
    """Tears down the singleton so a fresh subprocess is started next time.
    Only needed if a test deliberately wants an isolated server instance."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None
