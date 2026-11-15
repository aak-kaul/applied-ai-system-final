"""The Care Coach agent: parse -> guard -> retrieve -> draft -> schedule -> critique -> revise -> score -> (human approves) -> apply.

plan() never changes the owner's real data: it works on a sandbox copy and returns a PlanResult.
apply_plan() is the only function that writes, and it is idempotent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from pawpal_system import PRIORITY_RANK, Caregiver, Owner, Pet, Scheduler, Task, format_time, parse_time

from .guardrails import check_input, check_request
from .knowledge import Chunk, CheckRule, KnowledgeBase, TaskRule, load_knowledge
from .parser import CareRequest, parse_request
from .retriever import BM25Index

log = logging.getLogger("care_coach")

MAX_ROUNDS = 3
MIN_SCORE = 3.5  # BM25 score below which a retrieved chunk is not trusted
REVIEW_BELOW = 0.75  # confidence below this means "review carefully before applying"
STEP_MINS = 5
DEFAULT_HOURS = [("07:00", "21:00")]

TOPICS: Tuple[Tuple[str, str], ...] = (
    ("feeding", "feeding meals food"),
    ("walking", "walks exercise potty outdoors"),
    ("playing", "play fetch training enrichment exercise"),
    ("grooming", "grooming brushing coat bath nails"),
    ("water", "water bowl refresh"),
    ("litter", "litter box cleaning scooping"),
    ("hay", "hay greens pellets"),
    ("medication", "medication reminder prescribed"),
    ("schedule rules", "space meals apart keep exercise away from mealtimes"),
)
STAGE_WORDS = {
    ("dog", "baby"): "puppy", ("cat", "baby"): "kitten", ("rabbit", "baby"): "young baby",
    ("dog", "senior"): "senior older", ("cat", "senior"): "senior older", ("rabbit", "senior"): "senior older",
}


@dataclass
class PlanItem:
    """One planned occurrence of a care task, with the evidence behind it."""

    pet_name: str
    key: str
    title: str
    category: str
    priority: str
    frequency: str
    duration: int
    window: Tuple[str, str]
    chunk_id: str
    citation: str
    score: float
    reason: str
    earliest: str
    latest: str
    start: Optional[str] = None
    caregiver: Optional[str] = None
    task_id: Optional[str] = None
    problem: str = ""  # why it could not be scheduled / an unresolved issue

    @property
    def scheduled(self) -> bool:
        """True once the item has a start time."""
        return self.start is not None

    @property
    def end(self) -> Optional[str]:
        """End time as HH:MM, or None if unscheduled."""
        return format_time(parse_time(self.start) + self.duration) if self.start else None


@dataclass
class Violation:
    """A critic finding: which item to move and the tighter bounds that would fix it."""

    rule: str
    message: str
    item_index: int
    earliest: Optional[str] = None
    latest: Optional[str] = None


@dataclass
class PlanResult:
    """Everything the coach produced for one request (nothing here has touched real data)."""

    status: str  # ok | needs_review | clarify | escalate | refused
    message: str
    day: date
    request: Optional[CareRequest] = None
    items: List[PlanItem] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)
    critique_log: List[str] = field(default_factory=list)
    rounds: int = 0
    confidence: float = 0.0
    retrieved: List[Tuple[str, float]] = field(default_factory=list)
    advice: List[str] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)
    owner_name: str = "You"
    caregiver_windows: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def is_plan(self) -> bool:
        """True when a schedule was produced (as opposed to a question or a refusal)."""
        return self.status in ("ok", "needs_review")

    @property
    def scheduled_items(self) -> List[PlanItem]:
        """Items that got a time, in time order."""
        return sorted((i for i in self.items if i.scheduled), key=lambda i: (i.start, i.key))

    @property
    def unscheduled_items(self) -> List[PlanItem]:
        """Items the coach could not place."""
        return [i for i in self.items if not i.scheduled]

    def signature(self) -> List[tuple]:
        """Stable summary used for determinism checks."""
        return [(i.pet_name, i.key, i.title, i.start, i.caregiver, i.chunk_id) for i in self.items]

    def format(self) -> str:
        """Plain-text rendering used by the CLI."""
        if not self.is_plan:
            return f"[{self.status.upper()}] {self.message}"
        req = self.request
        lines = [
            f"PLAN for {req.pet_name or req.species} ({req.species}, {req.stage}) on {self.day:%A %Y-%m-%d}   "
            f"confidence {self.confidence:.2f}  status: {self.status.upper()}",
        ]
        for n in self.notices + req.assumptions:
            lines.append(f"  note: {n}")
        lines.append("  TIME         TASK                                         WHO         SOURCE")
        for i in self.scheduled_items:
            lines.append(f"  {i.start}-{i.end}  {i.title[:44]:<44} {(i.caregiver or '-')[:10]:<11} {i.citation}")
        for i in self.unscheduled_items:
            lines.append(f"  --:--        {i.title[:44]:<44} NOT PLACED   {i.problem}")
        lines.append(f"  critic: {self.rounds} revision round(s)")
        for entry in self.critique_log:
            lines.append(f"    - {entry}")
        for a in self.advice:
            lines.append(f"  tip: {a}")
        return "\n".join(lines)


def _first_sentence(text: str) -> str:
    cut = text.find(". ")
    return text[: cut + 1] if cut != -1 else text


class CareCoach:
    """Retrieval-augmented, self-checking planner over the PawPal+ scheduler."""

    def __init__(self, kb: Optional[KnowledgeBase] = None, max_rounds: int = MAX_ROUNDS) -> None:
        """Load the knowledge base (default folder) and build the BM25 index."""
        self.kb = kb or load_knowledge()
        self.max_rounds = max_rounds
        self._index = BM25Index(self.kb.chunks)

    # ------------------------------------------------------------------ #
    # public entry points
    # ------------------------------------------------------------------ #

    def plan(self, text: str, owner: Optional[Owner] = None, day: Optional[date] = None,
             use_custom: bool = True) -> PlanResult:
        """Run the whole agent loop and return a PlanResult. Does NOT modify `owner`."""
        day = day or date.today()
        trace: List[str] = []

        def step(name: str, detail: str) -> None:
            trace.append(f"{name}: {detail}")
            log.info("%s: %s", name, detail)

        guard = check_input(text)
        step("guardrail(input)", f"{guard.action}")
        if guard.action != "proceed":
            return PlanResult(_status_for(guard.action), guard.message, day, trace=trace)
        req = parse_request(guard.text)
        step("parse", f"pet={req.pet_name} species={req.species} stage={req.stage} conditions={sorted(req.conditions)} "
                      f"availability={req.availability}")
        guard2 = check_request(req)
        step("guardrail(request)", guard2.action)
        if guard2.action != "proceed":
            return PlanResult(_status_for(guard2.action), guard2.message, day, request=req, trace=trace)

        pet_name = req.pet_name or req.species.title()
        if not req.pet_name:
            req.assumptions.append(f"No pet name given, so I called the pet '{pet_name}'.")

        sandbox, windows = self._sandbox(owner, req, pet_name)
        scheduler = Scheduler(sandbox)
        step("sandbox", f"copied owner data; caregivers={[c.name for c in sandbox.caregivers]}")

        chunks, retrieved = self._retrieve(req, pet_name, use_custom)
        step("retrieve", f"{len(retrieved)} chunk(s): " + ", ".join(f"{c} ({s:.1f})" for c, s in retrieved))
        rules = self._select_rules(chunks)
        checks = self._select_checks(chunks)
        step("select", f"task rules={sorted(rules)} checks={sorted(c.key for c in checks)}")

        items = self._draft(pet_name, rules)
        step("draft", f"{len(items)} occurrence(s) from {len(rules)} rule(s)")
        for item in sorted(range(len(items)), key=lambda n: self._order(items[n])):
            self._place(items[item], scheduler, sandbox, day)
        step("schedule", f"placed {sum(i.scheduled for i in items)}/{len(items)}")

        log_lines: List[str] = []
        rounds = 0
        for rounds_used in range(1, self.max_rounds + 1):
            violations = self._critique(items, checks, req.species)
            if not violations:
                break
            changed = False
            for v in violations:
                moved = self._revise(v, items, scheduler, sandbox, day)
                log_lines.append(f"round {rounds_used}: {v.message} -> " + ("moved" if moved else "could not fix"))
                changed = changed or moved
            rounds = rounds_used
            step("critic", f"round {rounds_used}: {len(violations)} violation(s), changed={changed}")
            if not changed:
                break
        remaining = self._critique(items, checks, req.species)
        for v in remaining:
            items[v.item_index].problem = items[v.item_index].problem or v.message
            log_lines.append(f"unresolved: {v.message}")
        step("critic", f"final: {len(remaining)} unresolved violation(s)")

        advice = self._advice(items, chunks)
        confidence = self._confidence(items, remaining, req, rounds)
        missing_important = any(not i.scheduled and i.priority == "high" for i in items)
        status = "ok" if confidence >= REVIEW_BELOW and not remaining and not missing_important else "needs_review"
        step("score", f"confidence={confidence:.2f} -> {status}")
        message = "Plan ready for your approval." if status == "ok" else "Plan ready, but please review the flagged items before applying."
        return PlanResult(
            status, message, day, req, items, guard.notices, log_lines, rounds, confidence, retrieved, advice, trace,
            owner_name=sandbox.caregivers[0].name if sandbox.caregivers else "You", caregiver_windows=windows,
        )

    # ------------------------------------------------------------------ #
    # steps
    # ------------------------------------------------------------------ #

    def _sandbox(self, owner: Optional[Owner], req: CareRequest, pet_name: str):
        """Deep copy of the owner (or a fresh one) with the pet and at least one caregiver present."""
        sandbox = Owner.from_dict(owner.to_dict()) if owner is not None else Owner("You")
        windows: List[Tuple[str, str]] = []
        if sandbox.get_pet(pet_name) is None:
            sandbox.add_pet(Pet(pet_name, req.species, int(req.age_years or 0), ""))
        if not sandbox.caregivers:
            windows = list(req.availability) or list(DEFAULT_HOURS)
            if not req.availability:
                req.assumptions.append("No free hours given, so I assumed you are free 07:00-21:00.")
            sandbox.add_caregiver(Caregiver(sandbox.name, windows))
        elif req.availability:
            req.assumptions.append("Using the caregiver hours already saved in PawPal+ instead of the hours in the request.")
        return sandbox, windows or [w for c in sandbox.caregivers for w in c.availability]

    def _retrieve(self, req: CareRequest, pet_name: str, use_custom: bool):
        """Metadata-filtered BM25 per care topic; pet-scoped owner notes are always included."""
        eligible = [
            c for c in self.kb.eligible(req.species, req.stage, req.conditions, pet_name)
            if use_custom or not c.is_custom
        ]
        stage_word = STAGE_WORDS.get((req.species, req.stage), req.stage if req.stage != "adult" else "")
        picked: Dict[str, float] = {}
        for _topic, query in TOPICS:
            for chunk, score in self._index.search(f"{req.species} {stage_word} {query}", eligible, top_k=2):
                if score >= MIN_SCORE:
                    picked[chunk.chunk_id] = max(picked.get(chunk.chunk_id, 0.0), score)
        for chunk in eligible:  # explicit owner notes for this pet are never optional
            if chunk.applies.get("pet"):
                picked.setdefault(chunk.chunk_id, 0.0)
        by_id = {c.chunk_id: c for c in eligible}
        chunks = [by_id[cid] for cid in sorted(picked)]
        return chunks, sorted(((c.chunk_id, picked[c.chunk_id]) for c in chunks))

    @staticmethod
    def _select_rules(chunks: List[Chunk]) -> Dict[str, Tuple[TaskRule, Chunk]]:
        """One rule per key: the most specific chunk wins (owner notes > condition > stage > species)."""
        best: Dict[str, Tuple[tuple, TaskRule, Chunk]] = {}
        for chunk in chunks:
            for rule in chunk.tasks:
                rank = (chunk.specificity(), chunk.chunk_id)
                if rule.key not in best or rank > best[rule.key][0]:
                    best[rule.key] = (rank, rule, chunk)
        return {k: (r, c) for k, (_, r, c) in best.items()}

    @staticmethod
    def _select_checks(chunks: List[Chunk]) -> List[CheckRule]:
        seen: Dict[str, CheckRule] = {}
        for chunk in chunks:
            for check in chunk.checks:
                seen.setdefault(check.key, check)
        return list(seen.values())

    def _draft(self, pet_name: str, rules: Dict[str, Tuple[TaskRule, Chunk]]) -> List[PlanItem]:
        items: List[PlanItem] = []
        for key in sorted(rules):
            rule, chunk = rules[key]
            windows = rule.windows if rule.frequency == "Daily" else rule.windows[:1]
            for w in windows:
                items.append(PlanItem(
                    pet_name, rule.key, rule.title, rule.category, rule.priority, rule.frequency, rule.duration, w,
                    chunk.chunk_id, chunk.citation, 0.0, _first_sentence(chunk.text), w[0], w[1],
                ))
        return items

    @staticmethod
    def _order(item: PlanItem):
        return (PRIORITY_RANK[item.priority], item.window[0], item.key)

    def _find_slot(self, item: PlanItem, scheduler: Scheduler, day: date) -> Optional[Tuple[str, str]]:
        """First start in [earliest, latest] where no pending task overlaps and some caregiver is free."""
        busy = scheduler.get_schedule_for_day(day, include_completed=False)
        last_start = parse_time(item.latest) - item.duration
        for start in range(parse_time(item.earliest), last_start + 1, STEP_MINS):
            end = start + item.duration
            if any(t.start_minutes < end and start < t.end_minutes for t in busy):
                continue
            people = scheduler.available_caregivers(day, format_time(start), item.duration)
            if people:
                return format_time(start), people[0]
        return None

    def _place(self, item: PlanItem, scheduler: Scheduler, sandbox: Owner, day: date) -> bool:
        slot = self._find_slot(item, scheduler, day)
        if slot is None:
            item.start = item.caregiver = item.task_id = None
            item.problem = (f"nobody is free {item.earliest}-{item.latest} for {item.duration} min "
                            f"(consider asking a family member or a walker to cover this one)")
            return False
        task = Task(item.title, slot[0], item.duration, item.frequency, item.priority, item.category, day, slot[1])
        scheduler.schedule_task(item.pet_name, task)
        item.start, item.caregiver, item.task_id, item.problem = slot[0], slot[1], task.task_id, ""
        return True

    def _critique(self, items: List[PlanItem], checks: List[CheckRule], species: str) -> List[Violation]:
        """Apply the retrieved check rules to the current schedule."""
        found: List[Violation] = []
        for check in checks:
            if check.key == "meal_spacing":
                gap = int(check.get("min_gap", "180"))
                meals = sorted((n for n, i in enumerate(items) if i.key == "meal" and i.scheduled), key=lambda n: items[n].start)
                for a, b in zip(meals, meals[1:]):
                    spacing = parse_time(items[b].start) - parse_time(items[a].end)
                    if spacing < gap:
                        found.append(Violation(
                            "meal_spacing",
                            f"meals at {items[a].start} and {items[b].start} are only {spacing} min apart (need {gap})",
                            b, earliest=format_time(parse_time(items[a].end) + gap)))
            elif check.key == "exercise_after_meal" and species == "dog":
                gap, min_dur = int(check.get("gap", "60")), int(check.get("min_duration", "20"))
                meals = [i for i in items if i.key == "meal" and i.scheduled]
                for n, ex in enumerate(items):
                    if ex.category not in ("walk", "play") or not ex.scheduled or ex.duration < min_dur:
                        continue
                    for m in meals:
                        after = parse_time(ex.start) - parse_time(m.end)
                        if 0 <= after < gap:
                            found.append(Violation(
                                "exercise_after_meal",
                                f"'{ex.title}' at {ex.start} starts {after} min after the {m.start} meal (wait {gap})",
                                n, earliest=format_time(parse_time(m.end) + gap)))
                            break
        return found

    def _revise(self, v: Violation, items: List[PlanItem], scheduler: Scheduler, sandbox: Owner, day: date) -> bool:
        """Tighten the offending item's window and re-place it; restore it if no slot exists."""
        item = items[v.item_index]
        new_earliest = max(item.earliest, v.earliest) if v.earliest else item.earliest
        new_latest = min(item.latest, v.latest) if v.latest else item.latest
        if (new_earliest, new_latest) == (item.earliest, item.latest):
            return False  # bounds cannot get tighter: the rule cannot be satisfied inside this window
        pet = sandbox.get_pet(item.pet_name)
        old_task = pet.get_task(item.task_id)
        old = (item.start, item.caregiver, item.task_id, item.earliest, item.latest)
        pet.remove_task(item.task_id)
        item.earliest, item.latest = new_earliest, new_latest
        if self._place(item, scheduler, sandbox, day):
            return True
        pet.add_task(old_task)  # no slot satisfies the rule: put the task back where it was
        item.start, item.caregiver, item.task_id, item.earliest, item.latest = old
        item.problem = ""
        return False

    def _advice(self, items: List[PlanItem], chunks: List[Chunk]) -> List[str]:
        """Extra retrieved guidance: if something could not be placed, cite the 'share care' advice."""
        out: List[str] = []
        if any(not i.scheduled for i in items):
            hits = self._index.search("share care across people helper walker family cover", self.kb.chunks, top_k=1)
            if hits and hits[0][1] >= MIN_SCORE:
                out.append(f"{_first_sentence(hits[0][0].text)} ({hits[0][0].citation})")
        return out

    @staticmethod
    def _confidence(items: List[PlanItem], remaining: List[Violation], req: CareRequest, rounds: int) -> float:
        """Heuristic 0-1 score: starts at 1 and loses points for gaps, unresolved issues and guesses."""
        if not items:
            return 0.0
        score = 1.0
        score -= 0.5 * sum(not i.scheduled for i in items) / len(items)
        score -= 0.15 * len(remaining)
        score -= 0.05 * len(req.assumptions)
        score -= 0.02 * rounds
        return round(max(0.0, min(1.0, score)), 2)


def _status_for(action: str) -> str:
    return {"clarify": "clarify", "escalate": "escalate", "refuse": "refused"}[action]


# ---------------------------------------------------------------------- #
# applying an approved plan (the only place that writes)
# ---------------------------------------------------------------------- #

@dataclass
class ApplyResult:
    """What apply_plan changed."""

    added: List[Task] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def apply_plan(plan: PlanResult, owner: Owner) -> ApplyResult:
    """Write an approved plan into the real owner data. Safe to call twice: duplicates are skipped."""
    if not plan.is_plan:
        raise ValueError("Only a produced plan can be applied.")
    result = ApplyResult()
    req = plan.request
    pet_name = plan.items[0].pet_name if plan.items else (req.pet_name or req.species.title())
    if owner.get_pet(pet_name) is None:
        owner.add_pet(Pet(pet_name, req.species, int(req.age_years or 0), ""))
    if not owner.caregivers:
        owner.add_caregiver(Caregiver(plan.owner_name, plan.caregiver_windows or list(DEFAULT_HOURS)))
    scheduler = Scheduler(owner)
    pet = owner.get_pet(pet_name)
    for item in plan.scheduled_items:
        exists = any(
            t.description == item.title and t.due_time == item.start and t.due_date == plan.day and not t.is_completed
            for t in pet.tasks
        )
        if exists:
            result.skipped.append(f"{item.start} {item.title} (already scheduled)")
            continue
        task = Task(item.title, item.start, item.duration, item.frequency, item.priority, item.category, plan.day, item.caregiver)
        result.warnings.extend(scheduler.schedule_task(pet_name, task))
        result.added.append(task)
    return result
