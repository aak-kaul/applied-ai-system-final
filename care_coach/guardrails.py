"""Input guardrails. Everything here runs BEFORE any plan is drafted and is covered by tests.

Decisions: proceed | clarify (ask the owner) | escalate (emergency, point to a vet) | refuse (never invent doses).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

from .parser import CareRequest

MAX_CHARS = 1000
SUPPORTED = ("dog", "cat", "rabbit")

EMERGENCY_RE = re.compile(
    r"(not breathing|can'?t breathe|cannot breathe|struggling to breathe|difficulty breathing|trouble breathing|"
    r"seizure|collaps|unconscious|unresponsive|poison|overdos|swallowed|"
    r"\b(?:ate|eaten|swallowed|ingested|licked|drank)\b[^.!?]{0,40}\b(?:chocolate|grapes?|raisins?|xylitol|onions?|garlic|lil(?:y|ies)|antifreeze|pills?|rat poison|bleach)|"
    r"bleeding (?:a lot|heavily|badly|won'?t stop)|hit by (?:a )?car|swollen (?:hard )?belly|bloat|heat ?stroke|vomiting blood)",
    re.I,
)
DOSE_QUESTION_RE = re.compile(r"(how (?:much|many)|what (?:dose|dosage)|can i give|safe to give|should i give)", re.I)
DOSE_TOPIC_RE = re.compile(
    r"(dose|dosage|dosing|\bmg\b|milligram|ibuprofen|tylenol|acetaminophen|aspirin|advil|benadryl|medicine|medication|meds|pills?)",
    re.I,
)
DOSE_MENTION_RE = re.compile(r"(dose|dosage|dosing|\bmg\b|milligram)", re.I)
INJECTION_RE = re.compile(
    r"[^.!?\n]*(ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|prompts|rules)|"
    r"disregard (?:all |any |the )?(?:previous|prior|above|earlier)|reveal (?:your|the) (?:system )?prompt|you are now)[^.!?\n]*[.!?]?",
    re.I,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

EMERGENCY_MESSAGE = (
    "This sounds like it could be an emergency. Please contact a veterinarian or an emergency animal hospital right now "
    "instead of using a care schedule. In the United States, ASPCA Animal Poison Control is (888) 426-4435 and the "
    "Pet Poison Helpline is (855) 764-7661 (fees may apply; check the number before relying on it). "
    "PawPal+ Care Coach can build a routine once your pet is safe."
)
DOSE_MESSAGE = (
    "I can't suggest medication doses or say what is safe to give: that depends on your pet's weight, health and other "
    "medicines, and a wrong answer can be dangerous. Please ask your veterinarian or pharmacist. If you tell me the "
    "times your vet gave you, I can add a reminder (without any dose) to the schedule."
)


@dataclass
class GuardResult:
    """Outcome of the guardrail checks."""

    action: str = "proceed"  # proceed | clarify | escalate | refuse
    message: str = ""
    text: str = ""  # sanitised text to continue with
    notices: List[str] = field(default_factory=list)


def sanitize(raw: str) -> Tuple[str, List[str]]:
    """Strip control characters and instruction-like text, collapse whitespace."""
    notices: List[str] = []
    text = CONTROL_RE.sub(" ", raw or "")
    cleaned = INJECTION_RE.sub(" ", text)
    if cleaned != text:
        notices.append("Ignored instruction-like text in the request (the coach only reads pet and schedule details).")
    return re.sub(r"\s+", " ", cleaned).strip(), notices


def check_input(raw: str) -> GuardResult:
    """Checks that need only the raw text: empty, too long, emergency, dose question."""
    if raw is None or not str(raw).strip():
        return GuardResult("clarify", "Tell me about your pet, for example: \"Luna is a 4 year old dog. I'm free 7-9am and 5-8pm.\"")
    raw = str(raw)
    if len(raw) > MAX_CHARS:
        return GuardResult("refuse", f"That request is {len(raw)} characters long; please keep it under {MAX_CHARS}.")
    text, notices = sanitize(raw)
    if EMERGENCY_RE.search(text):
        return GuardResult("escalate", EMERGENCY_MESSAGE, text, notices)
    if DOSE_QUESTION_RE.search(text) and DOSE_TOPIC_RE.search(text):
        return GuardResult("refuse", DOSE_MESSAGE, text, notices)
    if DOSE_MENTION_RE.search(text):
        notices.append("I do not store or suggest doses: the schedule only gets a placeholder reminder, so follow your vet's label.")
    return GuardResult("proceed", "", text, notices)


def check_request(req: CareRequest) -> GuardResult:
    """Checks on the parsed request: species, ambiguity."""
    if req.multiple_species:
        return GuardResult("clarify", "I can plan for one pet at a time. Which pet should I plan for first (dog, cat or rabbit)?")
    if req.unsupported_species:
        return GuardResult(
            "clarify",
            f"I only have care guidance for dogs, cats and rabbits, so I can't plan for a {req.unsupported_species} reliably. "
            "A vet or an exotic-pet guide is the right source. If you meant a dog, cat or rabbit, tell me which.",
        )
    if req.species is None:
        return GuardResult("clarify", "What kind of pet is it (dog, cat or rabbit)? The care routine is different for each.")
    if req.ambiguous_ranges:
        return GuardResult(
            "clarify",
            f"I couldn't tell whether {', '.join(repr(r) for r in req.ambiguous_ranges)} means am or pm. "
            "Please write the hours like '6-9am' or '18:00-21:00'.",
        )
    return GuardResult("proceed")
