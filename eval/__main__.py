"""Eval pipeline CLI.

Usage:
    python -m eval
    python -m eval --system-prompt-path app/prompts/coordinator_system.md
    python -m eval --model gemini-2.0-flash --judge-model gemini-2.0-flash
    python -m eval --output report.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval.report import to_console_table, write_json_report
from eval.runner import run_eval


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the disruption-coordinator system prompt.")
    parser.add_argument("--system-prompt-path", type=Path, default=None, help="Override coordinator system prompt (for A/B testing).")
    parser.add_argument("--model", default=None, help="Override the coordinator/subagent model.")
    parser.add_argument("--judge-model", default=None, help="Override the LLM-judge model.")
    parser.add_argument("--output", type=Path, default=Path("eval_report.json"), help="Where to write the JSON report.")
    args = parser.parse_args()

    results = run_eval(
        system_prompt_path=args.system_prompt_path,
        model=args.model,
        judge_model=args.judge_model,
    )

    print(to_console_table(results))
    write_json_report(results, args.output)
    print(f"\nFull report written to {args.output.resolve()}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
