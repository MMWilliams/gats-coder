"""Four research workspaces sharing a proposal interface, not a truth rule."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from .lab_contracts import EvidenceStore, ProblemSpec
from .llm import extract_json


INSTRUCTIONS = {
    "coding": ("Propose an executable change, the relevant files, a failure "
               "hypothesis, and checks. Passing candidate-authored tests alone "
               "does not establish correctness."),
    "mathematics": ("Separate formal theorem and exact assumptions from "
                    "exploratory conjecture. Provide a proof artifact only if "
                    "available; never call a proof machine-checked yourself."),
    "science": ("State competing hypotheses, discriminating predictions, "
                "controls, provenance, uncertainty, and whether observations "
                "are simulation or empirical. Do not claim physical validity "
                "from a simulation alone."),
    "reasoning": ("Extract facts, constraints, alternative paths, a checkable "
                  "certificate when possible, and unresolved uncertainty."),
}


@dataclass(frozen=True)
class Proposal:
    problem_id: str
    domain: str
    answer: str
    assumptions: tuple[str, ...]
    verification_plan: str
    status: str = "hypothesized"


@dataclass(frozen=True)
class ExternalCheck:
    accepted: bool
    evaluator_id: str
    evidence_reference: str
    limitations: str = ""


def propose(problem: ProblemSpec, backend, store: EvidenceStore) -> Proposal:
    """Generate and archive a candidate; its status is always provisional."""
    prompt = ("You are a research worker in the " + problem.domain
              + " workspace. " + INSTRUCTIONS[problem.domain]
              + "\nRespond with only JSON keys answer, assumptions (list), "
              "verification_plan. Do not include any verified status.\n"
              "Problem contract:\n" + str(asdict(problem)))
    for attempt in range(3):
        data = extract_json(backend.complete(prompt))
        if (isinstance(data, dict) and isinstance(data.get("answer"), str)
                and isinstance(data.get("verification_plan"), str)
                and isinstance(data.get("assumptions"), list)):
            break
        store.append("proposal_rejected", {
            "problem_id": problem.problem_id, "attempt": attempt + 1,
            "reason": "missing structured answer, assumptions, or verification_plan",
            "backend": getattr(backend, "id", "unknown")})
        prompt += ("\n\nYour previous response was incomplete. Return ONLY a "
                   "valid JSON object with a string answer, list assumptions, "
                   "and string verification_plan. Do not certify your work.")
    else:
        raise ValueError("research worker did not return a complete proposal "
                         "after 3 attempts")
    proposal = Proposal(problem.problem_id, problem.domain, data["answer"],
                        tuple(str(a) for a in data["assumptions"]),
                        data["verification_plan"])
    store.append("proposal", proposal)
    return proposal


def check(proposal: Proposal, evaluator: Callable[[Proposal], ExternalCheck],
          store: EvidenceStore) -> ExternalCheck:
    """Record an evaluator response separately from the model's proposal.

    The caller owns `evaluator` and must keep it outside candidate control.
    A model's own string cannot be substituted for the callback's execution.
    """
    result = evaluator(proposal)
    if not isinstance(result, ExternalCheck) or not result.evaluator_id \
            or not result.evidence_reference:
        raise ValueError("external check lacks evaluator and evidence identity")
    store.append("external_check", {
        "problem_id": proposal.problem_id, "domain": proposal.domain,
        "accepted": result.accepted, "evaluator_id": result.evaluator_id,
        "evidence_reference": result.evidence_reference,
        "limitations": result.limitations})
    return result
