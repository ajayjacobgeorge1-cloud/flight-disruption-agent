"""Renders ScenarioResult lists as a console table and a JSON report file."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from tabulate import tabulate

from eval.runner import ScenarioResult


def to_console_table(results: list[ScenarioResult]) -> str:
    rows = []
    for r in results:
        if r.error:
            rows.append([r.scenario_id, "ERROR", "-", r.error[:60]])
            continue
        det = "PASS" if r.deterministic_passed else "FAIL"
        judge_score = r.judge_verdict.score_1_to_5 if r.judge_verdict else "-"
        overall = "PASS" if r.passed else "FAIL"
        failing_checks = ", ".join(c.name for c in r.deterministic_checks if not c.passed)
        note = failing_checks or (r.judge_verdict.rationale[:60] if r.judge_verdict else "")
        rows.append([r.scenario_id, f"{overall} (det={det}, judge={judge_score}/5)", "", note])
    return tabulate(rows, headers=["scenario", "result", "", "note"], tablefmt="github")


def to_json(results: list[ScenarioResult]) -> str:
    def verdict_dict(r: ScenarioResult):
        return asdict(r.judge_verdict) if r.judge_verdict else None

    payload = [
        {
            "scenario_id": r.scenario_id,
            "user_message": r.user_message,
            "final_text": r.final_text,
            "passed": r.passed,
            "error": r.error,
            "deterministic_checks": [asdict(c) for c in r.deterministic_checks],
            "judge_verdict": verdict_dict(r),
        }
        for r in results
    ]
    return json.dumps(payload, indent=2)


def write_json_report(results: list[ScenarioResult], path: Path) -> None:
    path.write_text(to_json(results), encoding="utf-8")
