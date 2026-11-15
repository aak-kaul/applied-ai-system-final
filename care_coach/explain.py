"""Plain-English explanation of a plan.

The default explainer is a template: deterministic, offline, and every sentence names its source.
LLMExplainer is an OPTIONAL wrapper (needs the `anthropic` package and an API key). It may only
reword the template; its answer is rejected unless every [citation] it uses is a chunk the plan
really retrieved, in which case we fall back to the template. The graded evidence runs never use it.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

from .planner import PlanResult

CITATION_RE = re.compile(r"\[([a-z_]+#[a-z0-9-]+)\]")


def template_explanation(plan: PlanResult) -> str:
    """One short paragraph per scheduled task: when, why, and where the advice came from."""
    if not plan.is_plan:
        return plan.message
    lines: List[str] = []
    for item in plan.scheduled_items:
        lines.append(
            f"- {item.start}-{item.end} {item.title} ({item.caregiver}): {item.reason} [{item.chunk_id}]"
        )
    for item in plan.unscheduled_items:
        lines.append(f"- NOT PLACED: {item.title}: {item.problem} [{item.chunk_id}]")
    return "\n".join(lines)


def valid_citations(text: str, plan: PlanResult) -> bool:
    """True if the text cites at least one chunk and every cited chunk was retrieved for this plan."""
    cited = set(CITATION_RE.findall(text))
    known = {cid for cid, _ in plan.retrieved}
    return bool(cited) and cited <= known


class LLMExplainer:
    """Optional: ask a model to reword the template. Falls back to the template on any problem."""

    def __init__(self, client: Any, model: str = "claude-sonnet-5-5", max_tokens: int = 600) -> None:
        """`client` is anything with `.messages.create(...)` (an anthropic.Anthropic instance, or a test double)."""
        self.client, self.model, self.max_tokens = client, model, max_tokens
        self.last_used_llm = False

    def explain(self, plan: PlanResult) -> str:
        """Return the model's wording if it is well-grounded, otherwise the deterministic template."""
        base = template_explanation(plan)
        self.last_used_llm = False
        if not plan.is_plan:
            return base
        prompt = (
            "Reword this pet-care schedule explanation in a friendly tone for a pet owner. Keep every time, task and "
            "[citation] exactly as written, add no new medical advice, doses or facts.\n\n" + base
        )
        try:
            reply = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, messages=[{"role": "user", "content": prompt}]
            )
            text = "".join(getattr(block, "text", "") for block in reply.content).strip()
        except Exception:  # network, auth, rate limit: never break the plan
            return base
        if text and valid_citations(text, plan):
            self.last_used_llm = True
            return text
        return base


def make_llm_explainer(api_key: Optional[str]) -> Optional[LLMExplainer]:
    """Build an LLMExplainer if the anthropic package and a key are available, else None."""
    if not api_key:
        return None
    try:
        import anthropic  # optional dependency
    except ImportError:
        return None
    return LLMExplainer(anthropic.Anthropic(api_key=api_key))
