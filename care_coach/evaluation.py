"""Evaluation harness: scripted cases, invariants that must hold for every plan, and a baseline comparison."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from pawpal_system import Caregiver, Owner, Pet, Scheduler, Task, format_time, parse_time

from .knowledge import load_knowledge
from .parser import parse_request
from .planner import DEFAULT_HOURS, CareCoach, PlanResult, apply_plan


@dataclass
class Row:
    """A normalised plan item so the coach and the baseline are scored identically."""

    key: str
    category: str
    start: int
    end: int
    title: str
    cited: bool


@dataclass
class CaseResult:
    """Outcome of one scripted case."""

    case_id: str
    status: str
    passed: bool
    failures: List[str] = field(default_factory=list)
    items: int = 0
    unscheduled: int = 0
    rounds: int = 0
    confidence: float = 0.0


def load_cases(path: str) -> dict:
    """Read the cases file."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_owner(spec: dict) -> Owner:
    """Build an Owner from the compact JSON description used in cases.json."""
    owner = Owner(spec["name"])
    for p in spec.get("pets", []):
        owner.add_pet(Pet(p["name"], p["species"], p.get("age", 1), ""))
    for c in spec.get("caregivers", []):
        owner.add_caregiver(Caregiver(c["name"], [tuple(w) for w in c["windows"]]))
    day = date.fromisoformat(spec.get("day", "2026-01-05"))
    for t in spec.get("tasks", []):
        owner.get_pet(t["pet"]).add_task(Task(t["description"], t["due_time"], t["duration"], due_date=day))
    return owner


def case_text(case: dict) -> str:
    """The request text of a case (supports a repeat spec for very long inputs)."""
    if "repeat" in case:
        return case["repeat"]["unit"] * case["repeat"]["times"]
    return case["text"]


def windows_for(owner: Optional[Owner], text: str) -> List[Tuple[int, int]]:
    """Caregiver availability (in minutes) that applies to this case."""
    if owner is not None and owner.caregivers:
        raw = [w for c in owner.caregivers for w in c.availability]
    else:
        raw = parse_request(text).availability or list(DEFAULT_HOURS)
    return [(parse_time(s), parse_time(e)) for s, e in raw]


def rows_from_plan(plan: PlanResult) -> List[Row]:
    """Scheduled items of a plan as Rows."""
    return [Row(i.key, i.category, parse_time(i.start), parse_time(i.end), i.title, bool(i.chunk_id)) for i in plan.scheduled_items]


def baseline_rows(day_text: str) -> List[Row]:
    """The one-size-fits-all routine used as the baseline: same for every pet, no retrieval, no checks.

    It is deliberately simple (a strawman): breakfast 07:30, walk 08:00, play 17:00, dinner 18:00.
    """
    spec = [("meal", "feeding", "07:30", 15, "Breakfast"), ("walk", "walk", "08:00", 30, "Walk"),
            ("play", "play", "17:00", 20, "Playtime"), ("meal", "feeding", "18:00", 15, "Dinner")]
    return [Row(k, c, parse_time(t), parse_time(t) + d, title, False) for k, c, t, d, title in spec]


def constraint_violations(rows: List[Row], species: str, meal_gap: int = 180, exercise_gap: int = 60, min_dur: int = 20) -> int:
    """Count breaches of the two knowledge-base scheduling rules (same numbers as knowledge/scheduling_principles.md)."""
    meals = sorted((r for r in rows if r.key == "meal"), key=lambda r: r.start)
    count = sum(1 for a, b in zip(meals, meals[1:]) if b.start - a.end < meal_gap)
    if species == "dog":
        for ex in rows:
            if ex.category in ("walk", "play") and ex.end - ex.start >= min_dur:
                count += sum(1 for m in meals if 0 <= ex.start - m.end < exercise_gap)
    return count


def feasible(rows: List[Row], windows: List[Tuple[int, int]], busy: List[Tuple[int, int]]) -> bool:
    """Every row inside some caregiver window, and no overlaps among rows or with existing tasks."""
    for r in rows:
        if not any(s <= r.start and r.end <= e for s, e in windows):
            return False
    spans = sorted([(r.start, r.end) for r in rows] + list(busy))
    return all(b[0] >= a[1] for a, b in zip(spans, spans[1:]))


def score_rows(rows: List[Row], case: dict, species: str, windows, busy) -> Dict[str, float]:
    """Metrics in [0, 1] (higher is better) used for the baseline comparison."""
    exp = case["expect"]
    wanted = exp.get("keys_scheduled", [])
    present = {r.key for r in rows}
    coverage = sum(k in present for k in wanted) / len(wanted) if wanted else 1.0
    species_fit = 0.0 if present & set(exp.get("keys_absent", [])) else 1.0
    return {
        "coverage": coverage,
        "species_fit": species_fit,
        "feasible": 1.0 if feasible(rows, windows, busy) else 0.0,
        "rules_ok": 1.0 if constraint_violations(rows, species) == 0 else 0.0,
        "grounded": (sum(r.cited for r in rows) / len(rows)) if rows else 0.0,
    }


def check_expectations(plan: PlanResult, exp: dict) -> List[str]:
    """Compare a plan with the case's expectations; return failure messages."""
    fails: List[str] = []
    if plan.status not in exp["status"]:
        fails.append(f"status {plan.status!r} not in {exp['status']}")
    if "message_contains" in exp and exp["message_contains"].lower() not in plan.message.lower():
        fails.append(f"message lacks {exp['message_contains']!r}")
    if plan.is_plan:
        keys = {i.key for i in plan.scheduled_items}
        titles = [i.title for i in plan.scheduled_items]
        for k in exp.get("keys_scheduled", []):
            if k not in keys:
                fails.append(f"expected a scheduled '{k}' task")
        for k in exp.get("keys_absent", []):
            if k in {i.key for i in plan.items}:
                fails.append(f"'{k}' should not be planned for this pet")
        for t in exp.get("titles_contain", []):
            if not any(t in x for x in titles + [i.title for i in plan.unscheduled_items]):
                fails.append(f"no task title contains {t!r}")
        for t in exp.get("titles_absent", []):
            if any(t in x for x in titles):
                fails.append(f"a task title contains {t!r}")
        if plan.rounds < exp.get("min_revision_rounds", 0):
            fails.append(f"expected >= {exp['min_revision_rounds']} critic revision round(s), got {plan.rounds}")
        if len(plan.unscheduled_items) < exp.get("min_unscheduled", 0):
            fails.append(f"expected >= {exp['min_unscheduled']} unplaced tasks, got {len(plan.unscheduled_items)}")
        if "caregiver_used" in exp and not any(i.caregiver == exp["caregiver_used"] for i in plan.scheduled_items):
            fails.append(f"nobody named {exp['caregiver_used']!r} was used")
        if "assumption_contains" in exp and not any(exp["assumption_contains"] in a for a in plan.request.assumptions):
            fails.append("expected assumption not recorded")
        if "notice_contains" in exp and not any(exp["notice_contains"] in n for n in plan.notices):
            fails.append("expected notice not recorded")
    else:
        if plan.items:
            fails.append("a non-plan outcome must not contain tasks")
    return fails


def check_invariants(coach: CareCoach, plan: PlanResult, case: dict, owner: Optional[Owner], day: date) -> List[str]:
    """Properties that must hold for EVERY plan regardless of the case."""
    fails: List[str] = []
    if not plan.is_plan:
        return fails
    known = {c.chunk_id for c in coach.kb.chunks}
    windows = windows_for(owner, case_text(case))
    busy = []
    if owner is not None:
        busy = [(t.start_minutes, t.end_minutes) for t in owner.get_all_tasks() if t.due_date == day and not t.is_completed]
    rows = rows_from_plan(plan)
    if not feasible(rows, windows, busy):
        fails.append("invariant: a task is outside caregiver hours or overlaps another task")
    for i in plan.items:
        if i.chunk_id not in known:
            fails.append(f"invariant: {i.title} cites unknown chunk {i.chunk_id}")
        if i.scheduled and not (parse_time(i.window[0]) <= parse_time(i.start) and parse_time(i.end) <= parse_time(i.window[1])):
            fails.append(f"invariant: {i.title} at {i.start} is outside its allowed window {i.window}")
    if constraint_violations(rows, plan.request.species) and plan.status == "ok":
        fails.append("invariant: status ok but a scheduling rule is still broken")
    # determinism: same input, same plan
    again = coach.plan(case_text(case), owner=owner, day=day, use_custom=case.get("use_custom", True))
    if again.signature() != plan.signature():
        fails.append("invariant: the plan is not deterministic")
    return fails


def check_apply(plan: PlanResult, owner: Optional[Owner], day: date) -> List[str]:
    """apply_plan must be idempotent and must leave the schedule conflict-free for the new tasks."""
    if not plan.is_plan:
        return []
    target = Owner.from_dict(owner.to_dict()) if owner is not None else Owner("You")
    first = apply_plan(plan, target)
    count_after_first = len(target.get_all_tasks())
    second = apply_plan(plan, target)
    fails = []
    if second.added or len(target.get_all_tasks()) != count_after_first:
        fails.append("invariant: applying the same plan twice added duplicates")
    if len(first.added) != len(plan.scheduled_items):
        fails.append("invariant: apply did not add every scheduled item")
    new_ids = {t.task_id for t in first.added}
    clashes = [w for w in Scheduler(target).detect_conflicts(day) if any(t.description in w for t in first.added)]
    if clashes and not (owner and owner.get_all_tasks()):
        fails.append("invariant: applied plan produced scheduling conflicts")
    return fails


def run_cases(path: str, coach: Optional[CareCoach] = None) -> Tuple[List[CaseResult], Dict[str, Dict[str, float]]]:
    """Run every case; returns per-case results and mean metrics for the coach vs the baseline."""
    data = load_cases(path)
    coach = coach or CareCoach()
    day = date.fromisoformat(data["day"])
    results: List[CaseResult] = []
    sums = {"coach": {}, "baseline": {}}
    compared = 0
    for case in data["cases"]:
        owner = build_owner({**data["owners"][case["owner"]], "day": data["day"]}) if "owner" in case else None
        before = owner.to_dict() if owner else None
        text = case_text(case)
        plan = coach.plan(text, owner=owner, day=day, use_custom=case.get("use_custom", True))
        fails = check_expectations(plan, case["expect"])
        fails += check_invariants(coach, plan, case, owner, day)
        fails += check_apply(plan, owner, day)
        if owner is not None and owner.to_dict() != before:
            fails.append("invariant: plan() changed the owner's real data")
        results.append(CaseResult(case["id"], plan.status, not fails, fails, len(plan.items), len(plan.unscheduled_items),
                                  plan.rounds, plan.confidence))
        if case.get("compare") and plan.is_plan:
            compared += 1
            species = plan.request.species
            windows = windows_for(owner, text)
            busy = [(t.start_minutes, t.end_minutes) for t in owner.get_all_tasks()] if owner else []
            for name, rows in (("coach", rows_from_plan(plan)), ("baseline", baseline_rows(text))):
                for metric, value in score_rows(rows, case, species, windows, busy).items():
                    sums[name][metric] = sums[name].get(metric, 0.0) + value
    means = {name: {m: round(v / compared, 2) for m, v in vals.items()} for name, vals in sums.items()}
    return results, means


def format_report(results: List[CaseResult], means: Dict[str, Dict[str, float]]) -> str:
    """Human-readable summary (also saved as Markdown)."""
    lines = ["| case | outcome | tasks | unplaced | revisions | confidence | result |", "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.case_id} | {r.status} | {r.items} | {r.unscheduled} | {r.rounds} | {r.confidence:.2f} | {'PASS' if r.passed else 'FAIL'} |")
    passed = sum(r.passed for r in results)
    lines.append("")
    lines.append(f"**{passed}/{len(results)} cases passed**")
    for r in results:
        for f in r.failures:
            lines.append(f"- FAIL {r.case_id}: {f}")
    lines += ["", "Specialized coach vs one-size-fits-all baseline (mean over the plan cases; 1.00 is best):", "",
              "| metric | baseline | coach |", "|---|---|---|"]
    for metric in means["coach"]:
        lines.append(f"| {metric} | {means['baseline'][metric]:.2f} | {means['coach'][metric]:.2f} |")
    return "\n".join(lines)
