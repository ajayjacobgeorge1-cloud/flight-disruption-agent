# Flight Disruption Agent

A multi-agent flight-disruption support system (coordinator → subagents →
synthesis → hook-gated rebooking), plus a Python eval pipeline that scores
the coordinator's system prompt against scripted scenarios.

```
User message (PNR + disruption text, possibly multi-concern)
        │
        ▼
 Coordinator agent  ── tool: "Task"
        │   (decomposes: status? rebooking? hotel? policy?)
        ├──► status-analyst subagent ──► get_booking, get_flight_status  ─┐
        ├──► options-finder subagent ──► search_alternatives             ├─► MCP (stdio) ──► mcp_server/server.py
        └──► policy-checker subagent ──► read_policy                     ─┘      (tools MCP server, its own process)
        │        (dispatched in parallel; findings passed back as tool results)
        ▼
 Synthesis (coordinator's own final turn: cites sources, flags conflicts, notes gaps)
        │
        ▼
 rebook_itinerary ──► MCP ──► mcp_server/server.py
        ▲
        └── PreToolUse hook (host-side, runs BEFORE the MCP call): fare diff over
            threshold? ──► deny + escalation ticket (call never reaches the server)
```

Every tool call -- from any subagent or the coordinator -- goes out over the
Model Context Protocol to a standalone server process (`mcp_server/server.py`),
not an in-process function call. `app/mcp_client.py` is the only thing that
talks to it; `app/tools.py` is a thin facade so the rest of the app didn't
need to change.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and replace `LLM_API_KEY=PLACEHOLDER_API_KEY` with the real key.
`LLM_API_BASE_URL` already points at the platform's OpenAI-compatible
`/v1/chat/completions` endpoint fronting Gemini.

## Running it

```powershell
python main.py "PNR ABC123, AA123 was cancelled. I need rebooking options and want to know if I get a hotel."
```

Prints the subagent dispatch trace, any coordinator-level tool calls
(`rebook_itinerary`), any escalation tickets created, and the final
synthesized response.

## Architecture notes

- **`app/llm_client.py`** — thin wrapper over the chat-completions endpoint.
  The endpoint speaks OpenAI wire format (`finish_reason`); this module
  normalizes it to the Anthropic-style `stop_reason` vocabulary
  (`tool_use` / `end_turn`) the rest of the app is written against, and
  supports swapping in a fake `transport` for offline tests (see `tests/`).
- **`mcp_server/server.py`** — a real MCP server (stdio transport, built
  with `FastMCP`) exposing 5 tools: `get_booking`, `get_flight_status`,
  `search_alternatives`, `read_policy`, `rebook_itinerary`, plus an
  admin-only `reset_mock_state`. Descriptions deliberately call out the
  boundary between adjacent tools (e.g. `get_flight_status` checks one
  flight's live status; `search_alternatives` searches a schedule for
  rebooking candidates; neither does the other's job) to avoid selection
  confusion. `mcp_server/tool_impl.py` holds the actual business logic
  (kept import-light so tests can call it directly without a subprocess);
  every function there raises a structured `ToolError` (`errorCategory`:
  transient/validation/permission, `isRetryable`) which the server
  converts into the JSON the client receives, whether the call succeeded
  or failed.
- **`app/mcp_client.py`** — the only thing in the app that speaks MCP. The
  SDK is async; this runs one persistent event loop on a background
  thread, launches `mcp_server/server.py` as a child process over stdio
  (once, as a process-wide singleton), and exposes blocking
  `list_tool_schemas()` / `call_tool()`.
- **`app/tools.py`** — a thin facade kept for backward compatibility with
  the rest of the app: `execute_tool(name, args)` now calls out over MCP
  instead of running an in-process function, and the schema constants
  (`GET_BOOKING_SCHEMA`, etc.) are discovered from the server's
  `list_tools()` response on first access rather than hand-written --
  `agent_loop.py`, `coordinator.py`, and `subagents.py` needed zero
  changes for this migration.
- **`app/hooks.py`** — a programmatic `PreToolUse` hook, not a prompt
  instruction, and not part of the MCP server. It runs host-side, before
  the `rebook_itinerary` call is even sent over MCP, and denies +
  escalates (creates a ticket in `app.state.ESCALATION_TICKETS`) any
  rebooking whose fare difference is at/above
  `REBOOKING_AUTONOMY_THRESHOLD_USD` -- the call never reaches the tool
  server. The model cannot reason its way around this.
- **`app/agent_loop.py`** — the generic agentic loop: calls the model,
  checks `stop_reason`, executes tool calls (through the hook) when it's
  `tool_use`, appends results, and loops; returns the final text once
  `stop_reason` is `end_turn`. Shared by every subagent.
- **`app/coordinator.py`** — same loop shape, but its only tools are
  `Task` (delegate to a named subagent) and `rebook_itinerary`. Multiple
  `Task` calls issued in the same model turn are dispatched concurrently
  with a thread pool, so `options-finder` and `policy-checker` run in
  parallel once `status-analyst` has reported back — matching the
  diagram. Findings are passed back to the coordinator as tool results
  (plain strings), never as shared mutable state.
- **`app/subagents.py`** — maps each subagent name to its own system
  prompt file (`app/prompts/*.md`) and restricted tool subset.
- **`app/prompts/*.md`** — system prompts as standalone files so the eval
  pipeline can swap in a variant without touching code.

## Eval pipeline (`eval/`)

```powershell
python -m eval
python -m eval --system-prompt-path app/prompts/coordinator_system.md --output report.json
```

For each scenario in `eval/scenarios.py` it:

1. Resets state (`app.tools.reset_test_state`) -- both the MCP server's
   mock booking backend and the host-side escalation-ticket queue -- so
   scenarios don't leak into each other.
2. Runs the full coordinator pipeline against the scenario's user message.
3. Checks **deterministic** expectations straight off the run trace:
   which subagents were dispatched, whether a tool that shouldn't have
   been called was called, whether escalation fired exactly when the
   fare-difference threshold says it should.
4. Sends the final response (plus a trace summary, for grounding) to an
   **LLM-judge** (`eval/judge.py`, Gemini via the same endpoint) that
   scores whether every concern was addressed, whether claims are
   grounded in tool results, and tone — returning strict JSON.
5. A scenario passes only if both the deterministic checks and the judge
   verdict pass; this keeps a prompt tweak from "fixing" the judge's
   wording while silently breaking the actual decomposition/escalation
   logic, or vice versa.

Use `--system-prompt-path` to A/B test a rewritten `coordinator_system.md`
against the same scenario suite before adopting it.

## Tests (no network / real API key required)

```powershell
.venv\Scripts\python -m pytest -q
```

`tests/fakes.py` provides a scripted fake `LLMClient` transport -- the
model is the only thing faked. `tests/test_agent_loop.py` and
`tests/test_coordinator.py` run against the *real* tools MCP server (a
local subprocess, started once and reused across the test session), so
they exercise the full `tool_use` → retry → `end_turn` loop, parallel
`Task` dispatch, and hook-driven escalation with tool calls genuinely
going out over MCP. `tests/test_mcp_integration.py` checks the MCP
transport itself (schema discovery, a raw tool round-trip). `tests/test_tools.py`
tests the business logic in `mcp_server/tool_impl.py` directly (no
subprocess, for speed), and `tests/test_hooks.py` covers the hook in
isolation. No step needs network access or a real `LLM_API_KEY`.
