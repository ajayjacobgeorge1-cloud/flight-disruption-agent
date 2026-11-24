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

import streamlit as st

from app.coordinator import run_coordinator
from app.config import SETTINGS
from app.state import ESCALATION_TICKETS
from app.tools import reset_test_state

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
            with st.expander("Agent trace"):
                st.markdown(msg["trace"])

user_message = st.chat_input(
    "e.g. PNR ABC123, AA123 was cancelled. I need rebooking options and want to know if I get a hotel."
)

if user_message:
    st.session_state.messages.append({"role": "user", "content": user_message})
    with st.chat_message("user"):
        st.markdown(user_message)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        accumulated = {"text": ""}

        def on_token(chunk: str) -> None:
            accumulated["text"] += chunk
            placeholder.markdown(accumulated["text"] + "▌")

        with st.spinner("Processing Your Request... Hang on"):
            result = run_coordinator(user_message, on_token=on_token)

        final_text = result.final_text or accumulated["text"]
        placeholder.markdown(final_text)

        trace_lines = []
        for call in result.subagent_calls:
            status = "FAILED" if call.failed else "ok"
            trace_lines.append(f"**{call.subagent_type}** ({status})")
            for tc in call.tool_calls:
                tag = " `[DENIED BY HOOK]`" if tc.denied_by_hook else ""
                trace_lines.append(f"- tool=`{tc.name}` args=`{tc.arguments}`{tag}")
            trace_lines.append(f"  findings: {call.findings[:300]}")

        if result.tool_calls:
            trace_lines.append("\n**Coordinator-level tool calls**")
            for tc in result.tool_calls:
                tag = " `[DENIED BY HOOK]`" if tc.denied_by_hook else ""
                trace_lines.append(f"- `{tc.name}`{tag}: {tc.result}")

        trace_md = "\n".join(trace_lines) if trace_lines else None
        if trace_md:
            with st.expander("Agent trace"):
                st.markdown(trace_md)

    st.session_state.messages.append(
        {"role": "assistant", "content": final_text, "trace": trace_md}
    )
