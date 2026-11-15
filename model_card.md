# Model Card: PawPal+ Care Coach

## 1. What this system is

A retrieval-augmented, self-checking planner that turns a plain-English description of a pet ("Mochi is a 10-week-old
puppy, I'm free 6-9am and after 6pm") into a one-day care schedule on top of the PawPal+ scheduler. **It contains no
trained or generative model.** "AI" here means: rule-based text parsing, BM25 retrieval over a hand-written knowledge
base, a critic that enforces rules retrieved from that knowledge base, and a heuristic confidence score. An optional
wrapper can ask Claude to reword the explanation; it is off by default and was not used for any result in this repo.

**Intended use:** a pet owner getting a *starting* routine to review and edit, and a course project demonstrating RAG,
an agentic loop and guardrails. **Not intended for:** diagnosing or treating a sick animal, medication dosing, emergencies,
exotic pets, or any use where a wrong schedule could cause harm without a human reading it first.

## 2. Limitations and biases

- **The knowledge base is the whole model, and it is small and written by one author.** About 37 chunks for dogs, cats and
  rabbits, written from general pet-care knowledge, not reviewed by a veterinarian. Windows (e.g. "walk 07:00-09:00") are
  reasonable defaults, not clinical facts. Individual pets vary by breed, size, health and household.
- **Coverage bias.** Only three species. Dogs have the most detailed rules (three life stages, a meal/exercise check);
  cats and rabbits are thinner. A user with a bird or reptile is told the system cannot help rather than getting a guess.
- **Cultural and lifestyle assumptions.** The default day assumes a household that can be home in the morning and evening,
  uses US clock conventions for am/pm, and gives US poison-hotline numbers (with a "verify" caveat). Shift workers, people with
  several caregivers or other time zones are served less well. "Free evenings" or "weekends" are not understood.
- **English-only, pattern-based parsing.** Names must be capitalised ("Luna", not "luna"); unusual phrasing can be missed.
  When the parser is unsure about am/pm it asks instead of guessing, but it can still misread a sentence it was not built for.
- **Age thresholds are crude.** Under 1 year is "baby"; senior starts at 8 (dog), 10 (cat), 6 (rabbit) regardless of breed or size.
- **Conflicts it cannot resolve.** If a rule cannot be satisfied inside its window (for example an existing 07:00 task pushes
  the meal so late that a walk an hour after it no longer fits), the plan is flagged `needs_review`; it does not search
  outside the windows.
- **Confidence is a heuristic, not a probability.** It drops for unplaced tasks, unresolved rule breaks and guessed inputs.
- **Evaluation limits.** 21 cases written by the same author as the system, a baseline that is deliberately naive, and no
  real users. Passing the harness shows internal consistency, not real-world correctness.
- **Optional LLM path is untested live** (only against a fake client), and a model could still produce wording that is
  subtly wrong while citing valid chunks.

## 3. Misuse and what prevents it

| Risk | What could go wrong | Mitigation in the code |
|---|---|---|
| Emergency treated as a scheduling problem | Owner of a poisoned or choking pet gets a calm routine | Emergency patterns (poisoning, breathing, seizure, collapse, "ate/swallowed X" for toxic items) stop the agent and show a "contact a vet now" message. Tested with several phrasings. |
| Medication doses | Wrong dose from an invented answer | Dose questions are refused; meds appear only as a placeholder reminder "follow the vet's label" with no dose. |
| Over-trust | Owner follows a schedule blindly | Every task shows its source; plans need explicit approval; low confidence or an unplaced high-priority task forces `NEEDS_REVIEW`; docs say it is not veterinary advice. |
| Prompt injection | Text like "ignore previous instructions" steering a future LLM step | Instruction-like sentences are stripped and reported; the optional LLM explainer may only reword and is rejected if it cites chunks that were not retrieved. |
| Silent data corruption | A bad plan overwriting real tasks | `plan()` runs on a copy; `apply_plan()` is the only writer and skips duplicates. |
| Unsafe or fabricated knowledge | A typo in a document changing advice | Rules are validated when loaded (categories, times, durations); the knowledge folder is plain markdown so changes are visible in git. |
| Using it for other people's data | Custom notes about someone else's pet | Custom notes are local files scoped by pet name; nothing is sent anywhere. |

**Not mitigated:** wrong or outdated guidance in the knowledge files, a user ignoring the warnings, and anyone editing
the knowledge files to add bad rules.

## 4. What testing surprised me with

All of these are real findings from building and running this project.

1. **The emergency guardrail had a hole.** My first pattern matched "ate chocolate" but not "ate a bar of chocolate". The
   `--demo` run produced a normal dog schedule for that sentence. I widened the pattern and added six phrasings plus a
   negative case ("Luna eats grapes as a treat" must not escalate) to the tests.
2. **Work hours were read as free hours.** "I work 9-5 but I'm free 6-9pm" made the parser flag "9-5" as ambiguous and ask
   a needless question. A unit test caught it; the parser now only reads text after "free/available".
3. **Low-scoring retrieval added noise.** A poisonous-foods chunk was retrieved for an ordinary adult-dog request at score
   2.8. Raising the minimum score to 3.5 removed it without losing any task chunk (a test confirms every task chunk is reachable).
4. **My critic did more than the document said.** I first added a "no exercise right *before* a meal" check; the knowledge
   text only says "after". I removed it so the critic enforces only what is written down and cited.
5. **Green on the first run was not proof.** All 21 cases passed immediately, so I broke the code five ways to see whether
   the harness noticed. It caught four clearly. Removing the life-stage filter was caught by only one case, so I added
   "must not contain" checks per stage.
6. **Honest failure beat forced success.** With an existing 07:00 task, the coach cannot satisfy "walk an hour after the
   meal" inside the 07:00-09:00 window. It reports this and asks for review.

## 5. Baseline vs specialized comparison

Both systems were scored on the same 7 plan cases with the same functions in `care_coach/evaluation.py`. The baseline
is a fixed routine for every pet (breakfast 07:30, walk 08:00, play 17:00, dinner 18:00) with no retrieval, no
availability check and no critic. It is a strawman by design.

Specialized coach vs one-size-fits-all baseline (mean over the plan cases; 1.00 is best):

| metric | baseline | coach |
|---|---|---|
| coverage | 0.55 | 1.00 |
| species_fit | 0.57 | 1.00 |
| feasible | 0.86 | 1.00 |
| rules_ok | 0.43 | 1.00 |
| grounded | 0.00 | 1.00 |

Reading it: the baseline misses species-specific tasks (no litter box, hay or grooming, and a "walk" for cats and
rabbits), ignores the owner's hours and breaks the meal/exercise rules for dogs, and cites nothing. The specialized coach
reaches 1.00 on all five because those are the properties it is built around, so the comparison demonstrates the value of
specialisation, not general superiority. Metrics: `coverage` = expected task kinds scheduled; `species_fit` = no task
kinds that do not belong; `feasible` = inside caregiver hours and no overlaps; `rules_ok` = both knowledge-base scheduling
rules satisfied; `grounded` = fraction of tasks with a citation.

## 6. AI collaboration

This project was built in conversation with Claude (Anthropic's AI assistant), which wrote most of the code and documents
under my direction. The full log is in [`ai_interactions.md`](ai_interactions.md).

**A helpful suggestion I accepted:** keep the AI feature deterministic and make the *agent loop* real: draft a schedule
in a sandbox copy of the owner's data, let a critic apply rules that are themselves retrieved from the knowledge base, and
revise. This made the project testable offline (21 cases, determinism, no side effects) and produced visible revisions
(for example, moving a walk from 07:15 to 08:15 after a 07:00 meal) instead of a one-shot answer.

**A flawed suggestion I rejected/revised:** the first version of the critic added an extra rule (no exercise within an hour
*before* a meal) that was not in the knowledge document, and the first emergency pattern missed "ate a bar of chocolate".
I removed the unsupported rule so everything the critic enforces is traceable to a cited source, and widened and tested the
emergency pattern. Lesson: the assistant's output looked complete both times; only running realistic inputs exposed the gaps.

**What I verified myself vs took on trust:** I ran the full test suite on my own Mac (Python 3.14.5, pytest 9.2.0) and
got 108 passed, and I ran `python run_eval.py` and got 21/21 cases passed (screenshot: `assets/pytest_and_eval_run.png`).
I also launched the Streamlit app with my existing pets and caregivers and confirmed that the new Care Coach section
appears and that the original availability, slot-finding and schedule features still work. I took on trust the care
guidance in the knowledge files, which Claude wrote from general pet-care knowledge and which no veterinarian has
reviewed, and most of the code, which I checked through the tests and the evaluation rather than by reading every line.
