"""PawPal+ Care Coach command line.

  python coach.py "Luna is a 4 year old dog. I'm free 7-9am and 5-8pm."
  python coach.py --demo                      # three plans + one guardrail example
  python coach.py "Biscuit is a 3 year old dog" --no-custom   # ignore the owner's own notes
  python coach.py "..." --data data.json --apply              # asks before writing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

from care_coach.explain import make_llm_explainer, template_explanation
from care_coach.planner import CareCoach, apply_plan
from pawpal_system import Owner, Scheduler

DEMO_REQUESTS = [
    "Luna is a 4 year old long-haired dog on medication. I'm free 07:00-21:00.",
    "I just adopted a 10-week-old puppy named Mochi. I work 9-5, so I'm only free 6-9am and after 6pm.",
    "Biscuit is a 3 year old dog. I'm free 07:00-21:00.",
    "My dog just ate a bar of chocolate, can you schedule his care?",
]


def setup_logging(path: str) -> None:
    """Send agent step logs to a file (kept out of the terminal output)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    logging.basicConfig(filename=path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def show(plan, trace: bool, explain: bool, explainer=None) -> None:
    """Print a plan (and optionally the agent trace and the explanation)."""
    print(plan.format())
    if explain and plan.is_plan:
        print("\nWhy this plan:")
        print(explainer.explain(plan) if explainer else template_explanation(plan))
    if trace:
        print("\nAgent trace:")
        for line in plan.trace:
            print("  " + line)


def main(argv=None) -> int:
    """Entry point; returns a process exit code."""
    ap = argparse.ArgumentParser(description="PawPal+ Care Coach")
    ap.add_argument("text", nargs="?", help="describe your pet and when you are free")
    ap.add_argument("--demo", action="store_true", help="run the built-in demo requests")
    ap.add_argument("--no-custom", action="store_true", help="ignore owner notes in knowledge/custom/")
    ap.add_argument("--trace", action="store_true", help="print the agent's step-by-step trace")
    ap.add_argument("--explain", action="store_true", help="print the per-task explanation")
    ap.add_argument("--llm", action="store_true", help="optional: reword the explanation with Claude (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--day", help="plan for this date, YYYY-MM-DD (default: today)")
    ap.add_argument("--data", help="load this PawPal+ JSON file first (and save to it with --apply)")
    ap.add_argument("--apply", action="store_true", help="after showing the plan, offer to add it to the schedule")
    ap.add_argument("--yes", action="store_true", help="with --apply, do not ask for confirmation")
    ap.add_argument("--log-file", default="logs/coach.log")
    args = ap.parse_args(argv)
    if not args.text and not args.demo:
        ap.print_help()
        return 2
    setup_logging(args.log_file)
    try:
        day = date.fromisoformat(args.day) if args.day else date.today()
    except ValueError:
        print("--day must look like 2026-01-05", file=sys.stderr)
        return 2
    coach = CareCoach()
    explainer = make_llm_explainer(os.environ.get("ANTHROPIC_API_KEY")) if args.llm else None
    if args.llm and explainer is None:
        print("(--llm needs `pip install anthropic` and ANTHROPIC_API_KEY; using the built-in explanation instead)")

    if args.demo:
        for number, text in enumerate(DEMO_REQUESTS, start=1):
            print(f"\n{'=' * 100}\nDEMO {number}: {text}\n{'=' * 100}")
            show(coach.plan(text, day=day, use_custom=not args.no_custom), args.trace, args.explain, explainer)
        return 0

    owner = None
    if args.data and os.path.exists(args.data):
        owner = Owner.load_from_json(args.data)
    plan = coach.plan(args.text, owner=owner, day=day, use_custom=not args.no_custom)
    show(plan, args.trace, args.explain, explainer)
    if args.apply and plan.is_plan:
        owner = owner or Owner("You")
        if args.yes or input("\nApply this plan to your schedule? [y/N] ").strip().lower() in ("y", "yes"):
            result = apply_plan(plan, owner)
            if args.data:
                owner.save_to_json(args.data)
            print(f"Added {len(result.added)} task(s); skipped {len(result.skipped)} duplicate(s).")
            for warning in result.warnings:
                print("  " + warning)
        else:
            print("Nothing was changed.")
    return 0 if plan.status != "refused" else 1


if __name__ == "__main__":
    sys.exit(main())
