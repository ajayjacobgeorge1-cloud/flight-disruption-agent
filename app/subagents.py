"""Subagent definitions: each gets its own system prompt and a restricted
tool set, matching the diagram's status-analyst / options-finder /
policy-checker split.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.agent_loop import AgentRunResult, run_agent_loop
from app.llm_client import LLMClient
from app.tools import (
    GET_BOOKING_SCHEMA,
    GET_FLIGHT_STATUS_SCHEMA,
    READ_POLICY_SCHEMA,
    SEARCH_ALTERNATIVES_SCHEMA,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


@dataclass
class SubagentSpec:
    name: str
    prompt_file: str
    tool_schemas: list[dict[str, Any]]


SUBAGENT_SPECS: dict[str, SubagentSpec] = {
    "status-analyst": SubagentSpec(
        name="status-analyst",
        prompt_file="status_analyst_system.md",
        tool_schemas=[GET_BOOKING_SCHEMA, GET_FLIGHT_STATUS_SCHEMA],
    ),
    "options-finder": SubagentSpec(
        name="options-finder",
        prompt_file="options_finder_system.md",
        tool_schemas=[SEARCH_ALTERNATIVES_SCHEMA],
    ),
    "policy-checker": SubagentSpec(
        name="policy-checker",
        prompt_file="policy_checker_system.md",
        tool_schemas=[READ_POLICY_SCHEMA],
    ),
}


def run_subagent(
    subagent_type: str,
    task_prompt: str,
    client: Optional[LLMClient] = None,
    system_prompt_override: Optional[str] = None,
) -> AgentRunResult:
    spec = SUBAGENT_SPECS.get(subagent_type)
    if spec is None:
        raise ValueError(f"Unknown subagent_type '{subagent_type}'. Valid: {list(SUBAGENT_SPECS)}")

    system_prompt = system_prompt_override or _load_prompt(spec.prompt_file)
    return run_agent_loop(
        system_prompt=system_prompt,
        user_message=task_prompt,
        tool_schemas=spec.tool_schemas,
        client=client,
    )
