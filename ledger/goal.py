"""Goal compiler (design SS7.1): LLM translates a natural-language task into
a goal contract -- but success criteria are *validated against the sensor
vocabulary* before anything runs. The LLM cannot invent propositions the
instruments can't verify; a hallucinated criterion is a hard error, not a
silent redefinition of success.
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import Catalog
from .llm import extract_json
from .state import Props

GOAL_PROMPT = """You are compiling a goal contract for a planning agent \
working on a code repository.

Task from the user:
{task}

The agent's success criteria MUST be chosen from this exact vocabulary of \
sensor-verifiable propositions (you may not invent new ones):
{vocab}

Pick the minimal set of propositions that, when all true, mean the task is \
done. Respond with ONLY a JSON object:
{{"goal": [<propositions>], "rationale": "<one sentence>"}}"""


class GoalCompileError(ValueError):
    pass


@dataclass(frozen=True)
class GoalContract:
    task_text: str
    goal: Props
    rationale: str
    backend_id: str


def catalog_vocabulary(catalog: Catalog) -> list[str]:
    """Every proposition mentioned by the action contracts -- the sensor-
    grounded namespace goals may draw from."""
    vocab: set = set()
    for a in catalog:
        vocab |= a.prec | a.add | a.delete
    return sorted(vocab)


# Propositions that are LEDGER's own bookkeeping, not outcomes anyone asked
# for. A compiled goal must never contain them: which patch slot happened to
# carry the fix is an implementation detail, so "patch.applied:a" makes a
# goal unsatisfiable the moment the planner uses slot b -- even with a green
# suite. Explicit goals passed by a caller are unaffected.
INTERNAL_PREFIXES = ("patch.queued:", "patch.applied:", "budget.",
                     "repo.branch:",
                     # A failure state is never something to aim for. Left
                     # in the vocabulary the compiler wrote
                     # "tests.fail_count:unit:1-5 AND tests.pass:unit" --
                     # a goal demanding the suite be red and green at once,
                     # unsatisfiable no matter how good the code is.
                     "tests.fail_count:", "tests.unknown:")


def goal_vocabulary(catalog: Catalog) -> list[str]:
    """The propositions a goal may be written in: outcomes only."""
    return [p for p in catalog_vocabulary(catalog)
            if not p.startswith(INTERNAL_PREFIXES)]


def compile_goal(task_text: str, vocabulary: list[str],
                 backend, attempts: int = 3,
                 initial: Props | None = None) -> GoalContract:
    """Compile the task into a goal contract, retrying a bad sample.

    Every other model call in an episode gets retries; this one used to get
    a single shot, so one malformed reply -- prose around the JSON, a
    hallucinated proposition -- killed the whole episode before it began.
    Smaller models produce that sample often enough to matter, and the
    correction is cheap: say what was wrong and ask again.

    `initial` is the state the episode starts in, and a goal already true
    there is rejected. The compiler once returned bare `repo.clean` for a
    "fix these two bugs" task: the tree was clean before anything ran, so
    the episode finished in zero steps and reported success having done
    nothing at all. An agent that verifies its own work cannot be allowed
    to pick a criterion that was already met.
    """
    base = GOAL_PROMPT.format(
        task=task_text,
        vocab="\n".join(f"- {p}" for p in vocabulary),
    )
    complaint = ""
    last = "goal compiler produced nothing usable"
    for attempt in range(max(1, attempts)):
        prompt = base if not complaint else (
            base + "\n\nYour previous reply was rejected: " + complaint +
            "\nReturn ONLY the JSON object -- no prose, no code fence.")
        try:
            reply = backend.complete(prompt)
        except Exception as exc:               # a dead backend is not ours
            raise
        obj = extract_json(reply)
        if obj is None:
            last = complaint = "it was not parseable JSON"
            continue
        goal = [str(p) for p in obj.get("goal", ())]
        if not goal:
            last = complaint = "the goal was empty"
            continue
        unknown = sorted(set(goal) - set(vocabulary))
        if unknown:
            last = complaint = (
                f"these are not sensor propositions: {unknown}. "
                "Choose only from the list above")
            continue
        if initial is not None and set(goal) <= set(initial):
            last = complaint = (
                f"every proposition in {sorted(goal)} is ALREADY TRUE before "
                "any work has been done, so the task would be considered "
                "complete immediately. Include the criteria that are "
                "currently false -- the ones the work has to make true")
            continue
        return _contract(task_text, goal, obj, backend)
    raise GoalCompileError(f"{last} after {attempts} attempts")


def _contract(task_text, goal, obj, backend) -> GoalContract:
    return GoalContract(
        task_text=task_text,
        goal=frozenset(goal),
        rationale=str(obj.get("rationale", "")),
        backend_id=getattr(backend, "id", "unknown"),
    )
