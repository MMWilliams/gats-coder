"""Research-controller records for agent and meta-level experiments.

Candidate construction uses Ledger episodes. This module makes the hypothesis,
budget, ablation, and selection trail explicit before protected evaluation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .lab_contracts import CandidateManifest, EvidenceStore
from .llm import extract_json


@dataclass(frozen=True)
class FailureRecord:
    episode_journal: str
    domain: str
    observed_failure: str
    artifact_digest: str
    resources_spent: int


@dataclass(frozen=True)
class Intervention:
    target_component: str
    hypothesis: str
    change: str
    discriminating_test: str
    ablation: str
    estimated_cost: float
    expected_gain: float
    information_value: float

    def __post_init__(self):
        if self.estimated_cost <= 0:
            raise ValueError("experiment cost must be positive")

    def acquisition(self, information_weight: float = 1.0) -> float:
        return ((self.expected_gain + information_weight * self.information_value)
                / self.estimated_cost)


def diagnose(failure: FailureRecord, backend, store: EvidenceStore) -> Intervention:
    """Obtain a falsifiable proposal; a model explanation is not a finding."""
    prompt = (
        "Diagnose this observed research failure. Distinguish observation "
        "from a hypothesis. Propose ONE modular change and a controlled "
        "experiment on unseen tasks with a matched budget and an ablation. "
        "Return only JSON fields target_component, hypothesis, change, "
        "discriminating_test, ablation, estimated_cost, expected_gain, "
        "information_value. Estimates are scheduling guesses, not results.\n"
        + str(asdict(failure)))
    data = extract_json(backend.complete(prompt))
    if not isinstance(data, dict):
        raise ValueError("diagnosis returned no structured intervention")
    try:
        intervention = Intervention(
            *(str(data[key]) for key in (
                "target_component", "hypothesis", "change",
                "discriminating_test", "ablation")),
            *(float(data[key]) for key in (
                "estimated_cost", "expected_gain", "information_value")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("diagnosis omitted a required experiment field") from exc
    store.append("failure", failure)
    store.append("intervention_proposed", intervention)
    return intervention


def register_candidate(store: EvidenceStore, manifest: CandidateManifest,
                       intervention: Intervention) -> str:
    """Archive lineage; no promotion follows from registration."""
    if manifest.target_component != intervention.target_component:
        raise ValueError("candidate target differs from tested hypothesis")
    return store.append("candidate", manifest)


def rank_experiments(experiments: list[Intervention],
                     information_weight: float = 1.0) -> list[Intervention]:
    """Deterministic scheduling heuristic, not an acceptance metric."""
    return sorted(experiments, key=lambda e: (-e.acquisition(information_weight),
                                               e.target_component, e.hypothesis))
