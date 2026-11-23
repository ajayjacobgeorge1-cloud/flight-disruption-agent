"""LLM-judge: scores the qualitative aspects a deterministic check can't
(tone, grounding, whether every concern was actually addressed in prose).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.config import SETTINGS
from app.llm_client import LLMClient

JUDGE_SYSTEM_PROMPT = """You are grading a customer-support AI agent's final response in a \
flight-disruption scenario. You will be given the customer's message, the scenario-specific \
thing to focus on, and the agent's final response text (plus a trace of what tools/subagents \
it used, for grounding -- do not reward claims not supported by the trace).

Score strictly. Respond with ONLY a JSON object, no markdown fences, matching this shape:
{"addressed_all_concerns": true|false, "grounded_in_tool_results": true|false, "tone_appropriate": true|false, "score_1_to_5": <int>, "rationale": "<2-3 sentences>"}
"""


@dataclass
class JudgeVerdict:
    addressed_all_concerns: bool
    grounded_in_tool_results: bool
    tone_appropriate: bool
    score_1_to_5: int
    rationale: str
    raw_content: str = ""
    parse_error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.addressed_all_concerns and self.grounded_in_tool_results and self.score_1_to_5 >= 3


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in judge output: {text!r}")
    return json.loads(match.group(0))


def judge_response(
    user_message: str,
    judge_focus: str,
    final_text: str,
    trace_summary: str,
    client: Optional[LLMClient] = None,
) -> JudgeVerdict:
    client = client or LLMClient(model=SETTINGS.eval_judge_model)

    user_prompt = (
        f"Customer message:\n{user_message}\n\n"
        f"What to focus on for this scenario:\n{judge_focus}\n\n"
        f"Trace of tools/subagents used (ground truth -- the agent's claims must be "
        f"consistent with this):\n{trace_summary}\n\n"
        f"Agent's final response to the customer:\n{final_text}\n"
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    response = client.chat(messages, temperature=0.0)
    try:
        parsed = _extract_json(response.content)
        return JudgeVerdict(
            addressed_all_concerns=bool(parsed.get("addressed_all_concerns", False)),
            grounded_in_tool_results=bool(parsed.get("grounded_in_tool_results", False)),
            tone_appropriate=bool(parsed.get("tone_appropriate", False)),
            score_1_to_5=int(parsed.get("score_1_to_5", 0)),
            rationale=str(parsed.get("rationale", "")),
            raw_content=response.content,
        )
    except Exception as exc:  # malformed judge output shouldn't crash the whole eval run
        return JudgeVerdict(
            addressed_all_concerns=False,
            grounded_in_tool_results=False,
            tone_appropriate=False,
            score_1_to_5=0,
            rationale="Judge output could not be parsed.",
            raw_content=response.content,
            parse_error=str(exc),
        )
