"""Streamlit UI for the flight-disruption coordinator agent.

Thin presentation layer on top of `app.coordinator.run_coordinator` -- it
does not duplicate any agent/tool/hook logic. The final synthesized
response streams in token-by-token via the `on_token` callback threaded
through `LLMClient.chat()` -> `run_coordinator()`; the subagent-dispatch
phase that precedes it is not itself streamed (it is a batch of tool_use
loop iterations, not a single text generation), so it is shown as a
spinner instead.

Run with:
    streamlit run streamlit_app.py
"""
from __future__ import annotations

import re
import sys

import streamlit as st

from app.agent_loop import looks_like_unfulfilled_plan
from app.coordinator import run_coordinator
from app.config import SETTINGS
from app.state import ESCALATION_TICKETS
from app.tools import reset_test_state

# Display-layer check, deliberately broader than the backend's own
# self-correction heuristic (app.agent_loop.looks_like_unfulfilled_plan):
# genuine customer-facing prose essentially never contains a literal
# bracketed span, so when the model's answer still has one (a narrated
# tool call, a sign-off placeholder like "[Your Name]", etc.) we don't put
# it in front of the customer -- it goes in the trace panel instead, in
# full, so nothing is actually lost.
_ANY_BRACKET_RE = re.compile(r"\[[^\[\]\n]{1,80}\]")

FALLBACK_MESSAGE = (
    "I wasn't able to put together a complete, verified answer for this "
    "request. You can see exactly what was attempted in **Agent trace & logs** "
    "below, or try asking again."
)


def is_customer_ready(text: str) -> bool:
    if not text or not text.strip():
        return False
    return not looks_like_unfulfilled_plan(text) and not _ANY_BRACKET_RE.search(text)

st.set_page_config(page_title="Flight Disruption Agent", page_icon="✈️")

st.title("✈️ Flight Disruption Agent")
st.caption(
    "Describe a PNR and disruption (cancellation, delay, etc.) -- the "
    "coordinator will dispatch subagents for status, rebooking options, "
    "and policy, then synthesize a response."
)

with st.sidebar:
    st.subheader("Session")
    st.write(f"**Model:** `{SETTINGS.llm_model}`")
    st.write(f"**Rebooking autonomy threshold:** ${SETTINGS.rebooking_autonomy_threshold_usd:,.0f}")
    if st.button("Reset conversation + backend state", use_container_width=True):
        reset_test_state()
        st.session_state.messages = []
        st.rerun()

    if ESCALATION_TICKETS:
        st.subheader("Escalation tickets")
        for ticket in ESCALATION_TICKETS:
            st.json(ticket, expanded=False)

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("trace"):
            with st.expander("\U0001f50d Agent trace & logs"):
                st.markdown(msg["trace"])

user_message = st.chat_input(
    "e.g. PNR ABC123, AA123 was cancelled. I need rebooking options and want to know if I get a hotel."
)

if user_message:
    # Prior turns only (plain role/content, no trace/internal scaffolding)
    # -- captured before appending the current turn below, so the model
    # has memory of what was already asked/answered without replaying its
    # own internal Task/tool-call noise from previous turns.
    conversation_history = [
        {"role": m["role"], "content": m["content"]} for m in st.session_state.messages
    ]

    st.session_state.messages.append({"role": "user", "content": user_message})
    with st.chat_message("user"):
        st.markdown(user_message)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        accumulated = {"text": ""}

        def on_token(chunk: str) -> None:
            accumulated["text"] += chunk
            placeholder.markdown(accumulated["text"] + "▌")

        try:
            with st.spinner("Processing Your Request... Hang on"):
                result = run_coordinator(
                    user_message, on_token=on_token, conversation_history=conversation_history
                )
        except Exception as exc:  # LLM/MCP call failed outright (e.g. gateway
            # error surviving every retry) -- never show a raw traceback to
            # the customer; it goes in the trace panel instead.
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (403, 429):
                display_text = (
                    "We're getting too many requests at the moment. Please "
                    "wait a little while and try again."
                )
            else:
                display_text = (
                    "Something went wrong while processing your request (a "
                    "backend/network error). Please try again -- see **Agent "
                    "trace & logs** below for the technical detail."
                )
            placeholder.markdown(display_text)
            with st.expander("\U0001f50d Agent trace & logs"):
                st.exception(sys.exc_info()[1])
            st.session_state.messages.append(
                {"role": "assistant", "content": display_text, "trace": "(see error above)"}
            )
            st.stop()

        final_text = result.final_text or accumulated["text"]
        display_text = final_text if is_customer_ready(final_text) else FALLBACK_MESSAGE
        placeholder.markdown(display_text)

        trace_lines = []
        for call in result.subagent_calls:
            status = "FAILED" if call.failed else "ok"
            trace_lines.append(f"**{call.subagent_type}** ({status})")
            for tc in call.tool_calls:
                tag = " `[DENIED BY HOOK]`" if tc.denied_by_hook else ""
                trace_lines.append(f"- tool=`{tc.name}` args=`{tc.arguments}`{tag}")
                trace_lines.append(f"  result: `{tc.result}`")
            trace_lines.append(f"- findings:\n```\n{call.findings}\n```")

        if result.tool_calls:
            trace_lines.append("\n**Coordinator-level tool calls**")
            for tc in result.tool_calls:
                tag = " `[DENIED BY HOOK]`" if tc.denied_by_hook else ""
                trace_lines.append(f"- `{tc.name}`{tag}: `{tc.result}`")

        trace_lines.append("\n**Raw final model output** (unedited, as generated)")
        trace_lines.append(f"```\n{final_text}\n```")

        trace_md = "\n".join(trace_lines)
        with st.expander("\U0001f50d Agent trace & logs"):
            st.markdown(trace_md)

    st.session_state.messages.append(
        {"role": "assistant", "content": display_text, "trace": trace_md}
    )
