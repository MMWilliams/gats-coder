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
                     "repo.branch:")


def goal_vocabulary(catalog: Catalog) -> list[str]:
    """The propositions a goal may be written in: outcomes only."""
    return [p for p in catalog_vocabulary(catalog)
            if not p.startswith(INTERNAL_PREFIXES)]


def compile_goal(task_text: str, vocabulary: list[str],
                 backend) -> GoalContract:
    prompt = GOAL_PROMPT.format(
        task=task_text,
        vocab="\n".join(f"- {p}" for p in vocabulary),
    )
    obj = extract_json(backend.complete(prompt))
    if obj is None:
        raise GoalCompileError("goal compiler returned no parseable JSON")
    goal = [str(p) for p in obj.get("goal", ())]
    if not goal:
        raise GoalCompileError("goal compiler returned an empty goal")
    unknown = sorted(set(goal) - set(vocabulary))
    if unknown:
        raise GoalCompileError(
            f"goal contains propositions outside the sensor vocabulary: "
            f"{unknown}")
    return GoalContract(
        task_text=task_text,
        goal=frozenset(goal),
        rationale=str(obj.get("rationale", "")),
        backend_id=getattr(backend, "id", "unknown"),
    )
