# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-agent flight-disruption support system: coordinator agent → parallel
subagents → synthesis → hook-gated rebooking. Plus a python3.12 eval pipeline
that scores the coordinator's system prompt against scripted scenarios. See
[README.md](README.md) for the full architecture diagram and a file-by-file
walkthrough — read it before making non-trivial changes, since the design
(MCP-only tool access, host-side hook, parallel `Task` dispatch) is load-bearing
and easy to accidentally break.

Key invariant: **every tool call goes out over MCP** to the standalone
`mcp_server/server.py` process — never as an in-process function call. If you
add a new tool, it belongs in `mcp_server/tool_impl.py` (business logic,
import-light, raises `ToolError`) and is registered in `mcp_server/server.py`;
`app/tools.py` discovers its schema automatically. Don't add a code path that
calls `mock_data.py` or `tool_impl.py` directly from `app/` — that bypasses the
MCP boundary the whole design is built around.

## Commands

Setup:
```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit LLM_API_KEY
```

Run the agent:
```bash
python3.12 main.py "PNR ABC123, AA123 was cancelled. I need rebooking options and want to know if I get a hotel."
```

Run tests (no network / real API key required):
```bash
.venv/bin/python3.12 -m pytest -q
```

Run a single test file / test:
```bash
.venv/bin/python3.12 -m pytest tests/test_coordinator.py -q
.venv/bin/python3.12 -m pytest tests/test_coordinator.py::test_name -q
```

Run the eval pipeline (scores the coordinator system prompt against scenarios
in `eval/scenarios.py`, deterministic checks + LLM-judge):
```bash
python3.12 -m eval
python3.12 -m eval --system-prompt-path app/prompts/coordinator_system.md --output report.json
```

Run the MCP tool server standalone (for manual inspection over stdio):
```bash
python3.12 -m mcp_server.server
```

Note: `.env.example` / README use PowerShell-style commands (this appears to
be developed partly on Windows); the above are the POSIX equivalents for the
same venv layout.

## Architecture

See [README.md](README.md) "Architecture notes" for the authoritative,
file-by-file explanation of `app/llm_client.py`, `app/mcp_client.py`,
`app/tools.py`, `app/hooks.py`, `app/agent_loop.py`, `app/coordinator.py`,
`app/subagents.py`, and `mcp_server/`. Highlights worth keeping in mind while
editing:

- **Hook, not prompt.** `app/hooks.py` is a programmatic `PreToolUse` hook
  that runs host-side, before `rebook_itinerary` ever reaches MCP. It denies
  and escalates (appends to `app.state.ESCALATION_TICKETS`) any rebooking
  with a fare difference at/above `REBOOKING_AUTONOMY_THRESHOLD_USD`. This is
  a business-rule enforcement point the model cannot reason around — don't
  move this logic into a system prompt.
- **Parallel subagent dispatch.** `app/coordinator.py` dispatches multiple
  `Task` tool calls issued in the same model turn concurrently via a thread
  pool. Subagent findings flow back as plain-string tool results, never as
  shared mutable state.
- **System prompts are data.** `app/prompts/*.md` are standalone files
  specifically so the eval pipeline can A/B a rewritten prompt via
  `--system-prompt-path` without touching code.
- **Two state stores, different lifetimes.** `app/state.py` holds host-side
  state (the escalation ticket queue); the mock booking backend lives behind
  the MCP server in `app/mock_data.py` and has its own process memory.
  `app.tools.reset_test_state` resets both together — use it between
  scenarios/tests rather than resetting just one.
- **Error shape.** Every function in `mcp_server/tool_impl.py` raises a
  structured `ToolError` (`errorCategory`: transient/validation/permission,
  `isRetryable`); `mcp_server/server.py` converts this into the JSON result
  the client sees. Keep new tool errors in this shape rather than raising
  bare exceptions.
- **Test tiers.** `tests/test_tools.py` and `tests/test_hooks.py` test logic
  directly with no subprocess (fast). `tests/test_agent_loop.py` and
  `tests/test_coordinator.py` run against the real MCP server as a local
  subprocess (started once, reused across the test session) with a scripted
  fake `LLMClient` transport from `tests/fakes.py` — the model is the only
  faked thing. `tests/test_mcp_integration.py` checks the MCP transport
  itself (schema discovery, raw tool round-trip).
