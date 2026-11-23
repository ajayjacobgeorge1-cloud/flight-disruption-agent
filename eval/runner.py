"""Runs every scenario against the coordinator, applies deterministic
checks against the trace, then an LLM-judge pass for qualitative review.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.coordinator import run_coordinator
from app.llm_client import LLMClient
from app.state import ESCALATION_TICKETS
from app.tools import reset_test_state
from eval.judge import JudgeVerdict, judge_response
from eval.scenarios import SCENARIOS, Scenario


@dataclass
class DeterministicCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    scenario_id: str
    user_message: str
    final_text: str
    deterministic_checks: list[DeterministicCheck] = field(default_factory=list)
    judge_verdict: Optional[JudgeVerdict] = None
    error: Optional[str] = None

    @property
    def deterministic_passed(self) -> bool:
        return all(c.passed for c in self.deterministic_checks)

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        judge_ok = self.judge_verdict.passed if self.judge_verdict else False
        return self.deterministic_passed and judge_ok


def _summarize_trace(result) -> str:
    lines = []
    for sc in result.subagent_calls:
        tool_names = ", ".join(tc.name for tc in sc.tool_calls) or "none"
        lines.append(f"- subagent={sc.subagent_type} tools=[{tool_names}] failed={sc.failed}")
    for tc in result.tool_calls:
        lines.append(f"- coordinator tool={tc.name} denied_by_hook={tc.denied_by_hook} result={json.dumps(tc.result)[:200]}")
    if ESCALATION_TICKETS:
        lines.append(f"- escalation_tickets={ESCALATION_TICKETS}")
    return "\n".join(lines) or "(no tool/subagent activity)"


def _check_deterministic(scenario: Scenario, result) -> list[DeterministicCheck]:
    checks: list[DeterministicCheck] = []

    dispatched = {sc.subagent_type for sc in result.subagent_calls}
    if scenario.expect_subagents:
        missing = scenario.expect_subagents - dispatched
        checks.append(
            DeterministicCheck(
                name="expected_subagents_dispatched",
                passed=not missing,
                detail=f"expected={scenario.expect_subagents} dispatched={dispatched} missing={missing}",
            )
        )

    if scenario.expect_no_tools:
        used_tools = {tc.name for sc in result.subagent_calls for tc in sc.tool_calls}
        used_tools |= {tc.name for tc in result.tool_calls}
        forbidden_hit = scenario.expect_no_tools & used_tools
        checks.append(
            DeterministicCheck(
                name="forbidden_tools_not_used",
                passed=not forbidden_hit,
                detail=f"forbidden={scenario.expect_no_tools} used={used_tools} violation={forbidden_hit}",
            )
        )

    if scenario.expect_escalation:
        checks.append(
            DeterministicCheck(
                name="escalation_ticket_created",
                passed=len(ESCALATION_TICKETS) > 0,
                detail=f"tickets={ESCALATION_TICKETS}",
            )
        )
    else:
        checks.append(
            DeterministicCheck(
                name="no_unexpected_escalation",
                passed=len(ESCALATION_TICKETS) == 0,
                detail=f"tickets={ESCALATION_TICKETS}",
            )
        )

    checks.append(
        DeterministicCheck(
            name="did_not_exceed_iteration_cap",
            passed=not result.hit_iteration_cap,
        )
    )
    return checks


def run_eval(
    system_prompt_override: Optional[str] = None,
    system_prompt_path: Optional[Path] = None,
    model: Optional[str] = None,
    judge_model: Optional[str] = None,
    scenarios: Optional[list[Scenario]] = None,
) -> list[ScenarioResult]:
    if system_prompt_path is not None:
        system_prompt_override = Path(system_prompt_path).read_text(encoding="utf-8")

    coordinator_client = LLMClient(model=model) if model else None
    judge_client = LLMClient(model=judge_model) if judge_model else None

    results: list[ScenarioResult] = []
    for scenario in scenarios or SCENARIOS:
        reset_test_state()
        try:
            run_result = run_coordinator(
                scenario.user_message,
                client=coordinator_client,
                system_prompt_override=system_prompt_override,
            )
            checks = _check_deterministic(scenario, run_result)
            trace_summary = _summarize_trace(run_result)
            verdict = judge_response(
                user_message=scenario.user_message,
                judge_focus=scenario.judge_focus,
                final_text=run_result.final_text,
                trace_summary=trace_summary,
                client=judge_client,
            )
            results.append(
                ScenarioResult(
                    scenario_id=scenario.id,
                    user_message=scenario.user_message,
                    final_text=run_result.final_text,
                    deterministic_checks=checks,
                    judge_verdict=verdict,
                )
            )
        except Exception as exc:
            results.append(
                ScenarioResult(
                    scenario_id=scenario.id,
                    user_message=scenario.user_message,
                    final_text="",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return results
