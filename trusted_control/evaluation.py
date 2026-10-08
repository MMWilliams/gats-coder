"""Constrained, domain-specific promotion decisions over external observations.

This module must run outside the candidate checkout in production. The caller
supplies measurements from independently controlled tests and pinned contracts;
candidate-authored reports alone are never sufficient evidence.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal

Domain = Literal["coding", "mathematics", "science", "reasoning"]


@dataclass(frozen=True)
class Contract:
    contract_id: str
    domain: Domain
    required_checks: frozenset[str]
    minimum_gain: float = 0.0
    regression_tolerance: float = 0.0
    min_replicates: int = 3

    def __post_init__(self):
        if self.min_replicates < 3 or self.regression_tolerance < 0:
            raise ValueError("invalid evaluation thresholds")


@dataclass(frozen=True)
class Observation:
    """External evaluator output for one frozen candidate and baseline."""

    contract_id: str
    candidate_id: str
    domain: Domain
    baseline: tuple[float, ...]
    candidate: tuple[float, ...]
    checks: frozenset[str]
    vetoes: frozenset[str] = frozenset()
    source: str = ""
    budget_matched: bool = False


@dataclass(frozen=True)
class Decision:
    approved: bool
    reasons: tuple[str, ...]
    lower_bound: float | None


@dataclass(frozen=True)
class ResearchTrial:
    """Protected final quality of the successor produced by a controller."""

    controller: str
    starting_agent: str
    seed: int
    research_budget: int
    task_budget: int
    starting_quality: float
    successor_quality: float
    evaluation_source: str


def compare_controllers(old: tuple[ResearchTrial, ...],
                        new: tuple[ResearchTrial, ...],
                        minimum_gain: float = 0.0) -> Decision:
    """Compare successor improvement on paired starting agents and seeds.

    Current task quality alone is not evidence that the *improver* improved.
    This requires fresh, externally measured successor quality at matched
    research and deployment budgets.
    """
    old_by_key = {(t.starting_agent, t.seed): t for t in old}
    new_by_key = {(t.starting_agent, t.seed): t for t in new}
    if len(old_by_key) != len(old) or len(new_by_key) != len(new) \
            or old_by_key.keys() != new_by_key.keys() or len(old) < 3:
        return Decision(False, ("at least three unique paired trials required",), None)
    older, newer = [], []
    for key in sorted(old_by_key):
        a, b = old_by_key[key], new_by_key[key]
        if not a.evaluation_source or not b.evaluation_source:
            return Decision(False, ("missing protected evaluation source",), None)
        if (a.research_budget, a.task_budget, a.starting_quality) != (
                b.research_budget, b.task_budget, b.starting_quality):
            return Decision(False, ("unmatched starting state or budgets",), None)
        older.append(a.successor_quality - a.starting_quality)
        newer.append(b.successor_quality - b.starting_quality)
    try:
        bound = lower_confidence_bound(tuple(older), tuple(newer))
    except ValueError as exc:
        return Decision(False, (str(exc),), None)
    if bound <= minimum_gain:
        return Decision(False, ("no supported gain in successor productivity",), bound)
    return Decision(True, (), bound)


def lower_confidence_bound(baseline: tuple[float, ...],
                           candidate: tuple[float, ...]) -> float:
    """Conservative normal-approximation paired bound for a prespecified trial.

    This is a screening statistic, not a sequential-testing guarantee. Fresh
    promotion data and multiplicity correction remain the evaluator's duty.
    """
    if len(baseline) != len(candidate) or len(baseline) < 3:
        raise ValueError("at least three paired measurements are required")
    differences = [new - old for old, new in zip(baseline, candidate)]
    if not all(math.isfinite(x) for x in differences):
        raise ValueError("non-finite performance measurement")
    return (statistics.mean(differences)
            - 1.96 * statistics.stdev(differences) / math.sqrt(len(differences)))


def judge(contract: Contract, observation: Observation,
          *, affected_domain: bool = True) -> Decision:
    reasons = []
    if observation.contract_id != contract.contract_id or observation.domain != contract.domain:
        reasons.append("evaluation contract or domain mismatch")
    if not observation.source:
        reasons.append("missing independent evidence provenance")
    if not observation.budget_matched:
        reasons.append("resource budgets differ")
    if observation.vetoes:
        reasons.append("veto: " + ", ".join(sorted(observation.vetoes)))
    missing = contract.required_checks - observation.checks
    if missing:
        reasons.append("missing mandatory checks: " + ", ".join(sorted(missing)))
    bound = None
    try:
        if len(observation.baseline) < contract.min_replicates:
            raise ValueError("insufficient independent replicates")
        bound = lower_confidence_bound(observation.baseline, observation.candidate)
        threshold = (contract.minimum_gain if affected_domain else
                     -contract.regression_tolerance)
        if bound <= threshold:
            reasons.append(f"lower bound {bound:.4g} does not exceed {threshold:.4g}")
    except ValueError as exc:
        reasons.append(str(exc))
    return Decision(not reasons, tuple(reasons), bound)


def judge_portfolio(contracts: dict[Domain, Contract],
                    observations: dict[Domain, Observation],
                    target_domains: frozenset[Domain]) -> Decision:
    """Shared changes must pass all covered domain regressions and vetoes."""
    if (not target_domains or not target_domains <= contracts.keys()
            or contracts.keys() != observations.keys()):
        return Decision(False, ("missing or mismatched domain evaluations",), None)
    reasons = []
    bounds = []
    for domain in sorted(contracts):
        decision = judge(contracts[domain], observations[domain],
                         affected_domain=domain in target_domains)
        bounds.append(decision.lower_bound)
        reasons.extend(f"{domain}: {reason}" for reason in decision.reasons)
    finite_bounds = [bound for bound in bounds if bound is not None]
    return Decision(not reasons, tuple(reasons),
                    min(finite_bounds) if finite_bounds else None)


DOMAIN_CHECKS: dict[Domain, frozenset[str]] = {
    "coding": frozenset({"build", "independent_tests", "regression", "security"}),
    "mathematics": frozenset({"pinned_checker", "statement_fidelity", "axiom_policy"}),
    "science": frozenset({"provenance", "controls", "uncertainty", "held_out_prediction"}),
    "reasoning": frozenset({"task_certificate", "independent_validator"}),
}


def domain_contract(identifier: str, domain: Domain, **thresholds) -> Contract:
    return Contract(identifier, domain, DOMAIN_CHECKS[domain], **thresholds)
