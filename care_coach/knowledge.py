"""Knowledge base for the Care Coach: markdown documents split into retrievable chunks.

Each ``## Heading`` in a document becomes one Chunk. Besides prose (which is what the
retriever searches) a chunk may carry machine-readable lines:

    applies: species=dog; stage=baby,adult; condition=medication; pet=Biscuit
    task:  key=meal | category=feeding | title=... | duration=15 | priority=high | frequency=Daily | windows=07:00-08:30,17:00-18:30
    check: key=meal_spacing | min_gap=180

``applies`` limits when a chunk may be used, ``task`` lines are care activities the
planner can schedule, and ``check`` lines are rules the critic enforces. Malformed
lines raise KnowledgeError so a broken document fails loudly instead of silently.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from pawpal_system import CATEGORIES, FREQUENCIES, PRIORITIES, parse_time

DEFAULT_KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")
APPLIES_KEYS = ("species", "stage", "condition", "pet")
WINDOW_RE = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")


class KnowledgeError(ValueError):
    """Raised when a knowledge document is malformed."""


@dataclass(frozen=True)
class TaskRule:
    """A schedulable care activity described by a knowledge chunk."""

    key: str
    category: str
    title: str
    duration: int
    priority: str
    frequency: str
    windows: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class CheckRule:
    """A rule the critic enforces on a draft plan (parameters are strings)."""

    key: str
    params: Tuple[Tuple[str, str], ...] = ()

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Return one parameter value."""
        return dict(self.params).get(name, default)


@dataclass
class Chunk:
    """One retrievable section of a knowledge document."""

    chunk_id: str
    source_file: str
    heading: str
    text: str
    applies: Dict[str, FrozenSet[str]] = field(default_factory=dict)
    tasks: List[TaskRule] = field(default_factory=list)
    checks: List[CheckRule] = field(default_factory=list)
    is_custom: bool = False

    @property
    def citation(self) -> str:
        """Human-readable source label, e.g. 'dog_care.md > Puppy feeding'."""
        return f"{self.source_file} > {self.heading}"

    def specificity(self) -> float:
        """How narrowly this chunk is targeted (more specific rules win a tie on the same key)."""
        score = 0.0
        if self.applies.get("pet"):
            score += 4
        score += 2 * len(self.applies.get("condition", ()))
        if self.applies.get("stage"):
            score += 1
        species = self.applies.get("species", frozenset())
        if species and species != frozenset({"any"}):
            score += 0.5
        return score

    def eligible_for(self, species: str, stage: str, conditions: FrozenSet[str], pet_name: str) -> bool:
        """True if the chunk's ``applies`` metadata permits using it for this pet."""
        a = self.applies
        if a.get("species") and "any" not in a["species"] and species not in a["species"]:
            return False
        if a.get("stage") and stage not in a["stage"]:
            return False
        if a.get("condition") and not a["condition"] <= conditions:
            return False
        if a.get("pet") and pet_name.strip().lower() not in a["pet"]:
            return False
        return True


def _kv(segment: str, where: str) -> Dict[str, str]:
    """Parse 'k=v | k=v' segments into a dict."""
    out: Dict[str, str] = {}
    for part in segment.split("|"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise KnowledgeError(f"{where}: expected key=value, got {part!r}")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_applies(value: str, where: str) -> Dict[str, FrozenSet[str]]:
    """Parse 'species=dog; stage=baby,adult' into {'species': {'dog'}, 'stage': {'baby','adult'}}."""
    result: Dict[str, FrozenSet[str]] = {}
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise KnowledgeError(f"{where}: bad applies entry {part!r}")
        k, v = (s.strip() for s in part.split("=", 1))
        if k not in APPLIES_KEYS:
            raise KnowledgeError(f"{where}: unknown applies key {k!r}")
        result[k] = frozenset(x.strip().lower() for x in v.split(",") if x.strip())
    return result


def parse_task(value: str, where: str) -> TaskRule:
    """Parse and validate one ``task:`` line."""
    f = _kv(value, where)
    missing = [k for k in ("key", "category", "title", "duration", "priority", "frequency", "windows") if k not in f]
    if missing:
        raise KnowledgeError(f"{where}: task missing {missing}")
    if f["category"] not in CATEGORIES:
        raise KnowledgeError(f"{where}: bad category {f['category']!r}")
    if f["priority"] not in PRIORITIES:
        raise KnowledgeError(f"{where}: bad priority {f['priority']!r}")
    if f["frequency"] not in FREQUENCIES:
        raise KnowledgeError(f"{where}: bad frequency {f['frequency']!r}")
    try:
        duration = int(f["duration"])
    except ValueError:
        raise KnowledgeError(f"{where}: duration must be an integer") from None
    if duration <= 0:
        raise KnowledgeError(f"{where}: duration must be positive")
    windows = []
    for w in f["windows"].split(","):
        w = w.strip()
        if not WINDOW_RE.match(w):
            raise KnowledgeError(f"{where}: bad window {w!r}")
        start, end = w.split("-")
        try:
            if parse_time(start) >= parse_time(end):
                raise KnowledgeError(f"{where}: window {w!r} must end after it starts")
        except ValueError as error:
            raise KnowledgeError(f"{where}: {error}") from None
        windows.append((start, end))
    return TaskRule(f["key"], f["category"], f["title"], duration, f["priority"], f["frequency"], tuple(windows))


def parse_check(value: str, where: str) -> CheckRule:
    """Parse one ``check:`` line."""
    f = _kv(value, where)
    if "key" not in f:
        raise KnowledgeError(f"{where}: check needs a key")
    key = f.pop("key")
    return CheckRule(key, tuple(sorted(f.items())))


def parse_document(text: str, source_file: str, is_custom: bool = False) -> List[Chunk]:
    """Split one markdown document into chunks (text before the first '## ' is ignored)."""
    chunks: List[Chunk] = []
    current: Optional[Chunk] = None
    prose: List[str] = []

    def close() -> None:
        if current is not None:
            current.text = " ".join(prose).strip()
            chunks.append(current)

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        where = f"{source_file}:{number}"
        if line.startswith("## "):
            close()
            heading = line[3:].strip()
            slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
            current = Chunk(f"{os.path.splitext(source_file)[0]}#{slug}", source_file, heading, "", is_custom=is_custom)
            prose = []
        elif current is None:
            continue
        elif line.startswith("applies:"):
            current.applies = parse_applies(line[len("applies:"):], where)
        elif line.startswith("task:"):
            current.tasks.append(parse_task(line[len("task:"):], where))
        elif line.startswith("check:"):
            current.checks.append(parse_check(line[len("check:"):], where))
        elif line:
            prose.append(line)
    close()
    return chunks


class KnowledgeBase:
    """All chunks loaded from a folder of markdown documents (including custom/ notes)."""

    def __init__(self, chunks: List[Chunk]) -> None:
        """Wrap a list of chunks; chunk ids must be unique."""
        ids = [c.chunk_id for c in chunks]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise KnowledgeError(f"duplicate chunk ids: {sorted(dupes)}")
        self.chunks = chunks

    def __len__(self) -> int:
        """Number of chunks."""
        return len(self.chunks)

    def sources(self) -> List[str]:
        """Sorted list of distinct source files."""
        return sorted({c.source_file for c in self.chunks})

    def eligible(self, species: str, stage: str, conditions: FrozenSet[str], pet_name: str) -> List[Chunk]:
        """Chunks whose ``applies`` metadata allows them for this pet."""
        return [c for c in self.chunks if c.eligible_for(species, stage, conditions, pet_name)]


def load_knowledge(root: str = DEFAULT_KB_DIR, include_custom: bool = True) -> KnowledgeBase:
    """Load every .md file under ``root``; files in a 'custom' folder are flagged as owner-supplied."""
    if not os.path.isdir(root):
        raise KnowledgeError(f"knowledge folder not found: {root}")
    chunks: List[Chunk] = []
    for folder, _dirs, files in sorted(os.walk(root)):
        custom = os.path.basename(folder) == "custom"
        if custom and not include_custom:
            continue
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(folder, name), "r", encoding="utf-8") as handle:
                chunks.extend(parse_document(handle.read(), name, is_custom=custom))
    if not chunks:
        raise KnowledgeError(f"no knowledge chunks found in {root}")
    return KnowledgeBase(chunks)
