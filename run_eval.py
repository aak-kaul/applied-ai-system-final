"""Run the evaluation harness:  python run_eval.py

Runs every case in eval/cases.json through the Care Coach, checks expectations and invariants,
compares against a generic baseline, prints a summary, writes logs/eval_results.{json,md},
and exits 1 if anything failed.
"""

import json
import os
import sys

from care_coach.evaluation import format_report, run_cases

CASES = os.path.join("eval", "cases.json")


def main() -> int:
    """Run and report."""
    results, means = run_cases(CASES)
    report = format_report(results, means)
    print(report)
    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "eval_results.md"), "w", encoding="utf-8") as handle:
        handle.write(report + "\n")
    with open(os.path.join("logs", "eval_results.json"), "w", encoding="utf-8") as handle:
        json.dump({"results": [r.__dict__ for r in results], "means": means}, handle, indent=2)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
