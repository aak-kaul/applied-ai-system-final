"""Turn a free-text request into a structured CareRequest (rule-based, no network, deterministic)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Tuple

SPECIES_WORDS = {
    "dog": {"dog", "dogs", "puppy", "puppies", "pup", "doggo", "canine"},
    "cat": {"cat", "cats", "kitten", "kittens", "kitty", "feline"},
    "rabbit": {"rabbit", "rabbits", "bunny", "bunnies"},
}
BABY_WORDS = {"puppy", "puppies", "pup", "kitten", "kittens"}
UNSUPPORTED_WORDS = {
    "bird", "parrot", "parakeet", "budgie", "cockatiel", "hamster", "gerbil", "guinea", "snake", "lizard",
    "gecko", "turtle", "tortoise", "fish", "goldfish", "ferret", "horse", "chicken", "rat", "iguana", "chinchilla",
}
SENIOR_AGE = {"dog": 8.0, "cat": 10.0, "rabbit": 6.0}  # years at which the 'senior' rules start

NOT_NAMES = {
    "I", "My", "It", "This", "That", "He", "She", "They", "We", "There", "Here", "Please", "Plan", "Can", "Could",
    "Help", "Make", "Create", "Need", "Want", "Just", "Also", "And", "But", "So", "The", "A", "An", "Monday",
    "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Today", "Tomorrow", "Ignore",
}
MEDICATION_RE = re.compile(r"\b(medication|medicine|meds|pills?|prescri\w+|insulin|antibiotics?)\b", re.I)
LONG_HAIR_RE = re.compile(r"\b(long[- ]?haired|long hair|long coat(?:ed)?|longhaired)\b", re.I)
AGE_RE = re.compile(r"(\d+(?:\.\d+)?)[\s-]*(weeks?|wks?|months?|mos?|years?|yrs?)(?:[\s-]*old)?\b", re.I)
NAME_PATTERNS = (
    re.compile(r"\b(?:named|called)\s+([A-Z][A-Za-z'-]+)"),
    re.compile(r"\b([A-Z][a-z][A-Za-z'-]*)\s+is\s+(?:a|an|my)\b"),
    re.compile(r"\bmy\s+(?:[\w-]+\s+){0,3}?(?:dog|puppy|cat|kitten|rabbit|bunny)\s+([A-Z][a-z][A-Za-z'-]*)"),
    re.compile(r"\b(?:plan|care|schedule)(?:\s+(?:care|a plan|a routine))?\s+for\s+([A-Z][a-z][A-Za-z'-]*)"),
)
RANGE_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|–|—|to|until)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I
)
AFTER_RE = re.compile(r"\bafter\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
BEFORE_RE = re.compile(r"\bbefore\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
AVAIL_WORDS_RE = re.compile(r"\b(free|available|home|around|off)\b", re.I)
CONTINUATION_RE = re.compile(r"^\s*(?:and|or|also|then|plus)?\s*(?:after|before|\d)", re.I)

DAY_START, DAY_END = "06:00", "21:00"  # bounds used for 'before X' / 'after X'


@dataclass
class CareRequest:
    """What the coach understood from the request."""

    raw: str
    pet_name: Optional[str] = None
    species: Optional[str] = None
    unsupported_species: Optional[str] = None
    multiple_species: bool = False
    age_years: Optional[float] = None
    stage: Optional[str] = None  # baby | adult | senior
    conditions: FrozenSet[str] = frozenset()
    availability: List[Tuple[str, str]] = field(default_factory=list)
    ambiguous_ranges: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)


def _hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _to_24h(hour: int, minute: int, meridiem: str) -> Optional[int]:
    """Minutes after midnight for a 12-hour clock reading, or None if invalid."""
    if not (1 <= hour <= 12 and 0 <= minute < 60):
        return None
    return ((hour % 12) + (12 if meridiem.lower() == "pm" else 0)) * 60 + minute


def _fmt(minutes: int) -> str:
    return _hhmm(minutes // 60, minutes % 60)


def parse_range(match: re.Match) -> Optional[Tuple[str, str]]:
    """Convert one regex range match to ('HH:MM', 'HH:MM'); None means 'cannot tell am from pm'."""
    s_h, s_m, s_mer, e_h, e_m, e_mer = match.groups()
    s_hour, e_hour = int(s_h), int(e_h)
    s_min, e_min = int(s_m or 0), int(e_m or 0)
    if s_mer is None and e_mer is None:
        both_clock = s_m is not None and e_m is not None
        leading_zero = s_h.startswith("0") or e_h.startswith("0")
        if both_clock and (leading_zero or s_hour > 12 or e_hour > 12):  # unmistakable 24-hour form
            if not (0 <= s_hour <= 23 and 0 <= e_hour <= 23 and s_min < 60 and e_min < 60):
                return None
            start, end = s_hour * 60 + s_min, e_hour * 60 + e_min
            return (_fmt(start), _fmt(end)) if start < end else None
        return None
    end_mer = e_mer or s_mer
    end = _to_24h(e_hour, e_min, end_mer)
    start_mer = s_mer or end_mer
    start = _to_24h(s_hour, s_min, start_mer)
    if start is None or end is None:
        return None
    if s_mer is None and start >= end:  # "9-5pm" -> 09:00-17:00
        start = _to_24h(s_hour, s_min, "am" if start_mer == "pm" else "pm")
    if e_mer is None and end <= start:  # "6am-9" -> 06:00-09:00 is fine; "9am-2" -> 14:00
        end = _to_24h(e_hour, e_min, "pm" if end_mer == "am" else "am")
    if start is None or end is None or start >= end:
        return None
    return _fmt(start), _fmt(end)


def parse_availability(text: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Find the hours the owner says they are free. Returns (windows, ambiguous_snippets)."""
    windows: List[Tuple[str, str]] = []
    ambiguous: List[str] = []
    previous_was_availability = False
    for clause in re.split(r"[.;!?\n,]", text):
        keyword = AVAIL_WORDS_RE.search(clause)
        is_avail = bool(keyword) or (previous_was_availability and bool(CONTINUATION_RE.match(clause)))
        previous_was_availability = is_avail
        if not is_avail:
            continue
        if keyword:  # only read what comes AFTER 'free'/'available' ("I work 9-5 but I'm free 6-9pm")
            clause = clause[keyword.start():]
        for m in RANGE_RE.finditer(clause):
            parsed = parse_range(m)
            if parsed:
                windows.append(parsed)
            else:
                ambiguous.append(m.group(0).strip())
        for m in AFTER_RE.finditer(clause):
            start = _to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3))
            if start is not None and start < 21 * 60:
                windows.append((_fmt(start), DAY_END))
        for m in BEFORE_RE.finditer(clause):
            end = _to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3))
            if end is not None and end > 6 * 60:
                windows.append((DAY_START, _fmt(end)))
    return sorted(set(windows)), ambiguous


def parse_age_years(text: str) -> Optional[float]:
    """Age in years from phrases like '10-week-old', '3 months', '4 year old'."""
    for m in AGE_RE.finditer(text):
        value, unit = float(m.group(1)), m.group(2).lower()
        if unit.startswith(("week", "wk")):
            return value / 52
        if unit.startswith(("month", "mo")):
            return value / 12
        return value
    return None


def parse_name(text: str) -> Optional[str]:
    """First plausible capitalised pet name, or None."""
    for pattern in NAME_PATTERNS:
        for m in pattern.finditer(text):
            candidate = m.group(1)
            if candidate not in NOT_NAMES and candidate.lower() not in {w for s in SPECIES_WORDS.values() for w in s}:
                return candidate
    return None


def parse_request(text: str) -> CareRequest:
    """Parse already-sanitised text into a CareRequest."""
    req = CareRequest(raw=text)
    words = set(re.findall(r"[a-z]+", text.lower()))
    found = [s for s, vocab in SPECIES_WORDS.items() if words & vocab]
    unsupported = sorted(words & UNSUPPORTED_WORDS)
    if len(found) + (1 if unsupported else 0) > 1:
        req.multiple_species = True
    elif found:
        req.species = found[0]
    elif unsupported:
        req.unsupported_species = "guinea pig" if unsupported[0] == "guinea" else unsupported[0]

    req.pet_name = parse_name(text)
    req.age_years = parse_age_years(text)
    conditions = set()
    if MEDICATION_RE.search(text):
        conditions.add("medication")
    if LONG_HAIR_RE.search(text):
        conditions.add("long-haired")
    req.conditions = frozenset(conditions)

    if req.species:
        if words & BABY_WORDS or (req.age_years is not None and req.age_years < 1.0):
            req.stage = "baby"
        elif req.age_years is not None and req.age_years >= SENIOR_AGE[req.species]:
            req.stage = "senior"
        elif req.age_years is not None:
            req.stage = "adult"
        else:
            req.stage = "adult"
            req.assumptions.append("No age given, so I assumed an adult " + req.species + ".")
    req.availability, req.ambiguous_ranges = parse_availability(text)
    return req
