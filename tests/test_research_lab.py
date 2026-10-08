import json

import pytest

from ledger.lab_contracts import Claim, EvidenceStore, ProblemSpec
from ledger.domain_workers import ExternalCheck, check, propose
from ledger.llm import FakeBackend
from ledger.improvement import FailureRecord, diagnose, rank_experiments
from trusted_control.evaluation import (DOMAIN_CHECKS, Observation, ResearchTrial,
                                        compare_controllers, domain_contract, judge,
                                        judge_portfolio)


def test_evidence_keeps_claim_status_and_detects_rewrite(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.jsonl")
    problem = ProblemSpec("p1", "mathematics", (), ("n > 0",), (),
                          "proof", 200, "fixed-formal-v1")
    store.append("problem", problem)
    store.append("claim", Claim("c1", "all n satisfy P", "mathematics",
                                 "simulation_supported", ("n > 0",), ("trial-4",)))
    assert store.read()[1]["payload"]["status"] == "simulation_supported"
    path = tmp_path / "evidence.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = json.loads(lines[0])
    changed["payload"]["required_output"] = "weaker theorem"
    path.write_text(json.dumps(changed) + "\n" + lines[1] + "\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="modified"):
        store.append("claim", {"id": "c2"})


@pytest.mark.parametrize("domain", ["coding", "mathematics", "science", "reasoning"])
def test_domain_evaluator_requires_independent_checks(domain):
    contract = domain_contract("pinned-v1", domain)
    observation = Observation("pinned-v1", "candidate-1", domain,
                              (0, 0, 0, 0), (2, 2, 2, 2),
                              DOMAIN_CHECKS[domain], source="trusted-run-42",
                              budget_matched=True)
    assert judge(contract, observation).approved
    missing = Observation("pinned-v1", "candidate-1", domain,
                          observation.baseline, observation.candidate,
                          frozenset(), source="", budget_matched=True)
    result = judge(contract, missing)
    assert not result.approved
    assert any("mandatory checks" in r for r in result.reasons)


def test_veto_cannot_be_traded_for_performance():
    contract = domain_contract("fixed", "coding")
    result = judge(contract, Observation(
        "fixed", "c", "coding", (0, 0, 0), (100, 100, 100),
        DOMAIN_CHECKS["coding"], frozenset({"evidence tampering"}),
        source="independent-run", budget_matched=True))
    assert result.lower_bound == 100
    assert not result.approved


@pytest.mark.parametrize("domain", ["coding", "mathematics", "science", "reasoning"])
def test_each_worker_proposes_without_self_certification(tmp_path, domain):
    store = EvidenceStore(tmp_path / "research.jsonl")
    problem = ProblemSpec("p1", domain, (), (), (), "artifact", 10, "trusted-v1")

    def response(prompt):
        assert f"{domain} workspace" in prompt
        return json.dumps({"answer": "candidate", "assumptions": [],
                           "verification_plan": "check independently",
                           "status": "formally_checked"})

    candidate = propose(problem, FakeBackend(response), store)
    assert candidate.status == "hypothesized"
    assert store.read()[0]["payload"]["status"] == "hypothesized"
    result = check(candidate,
                   lambda p: ExternalCheck(False, "independent-checker-v1",
                                           "run-123", "failed validation"), store)
    assert not result.accepted
    assert store.read()[1]["kind"] == "external_check"


def test_meta_improvement_uses_successor_quality_at_matched_budgets():
    def trials(controller, gain, budget=100):
        return tuple(ResearchTrial(controller, f"agent-{n}", n, budget, 20,
                                   2, 2 + gain, f"protected-{n}")
                     for n in range(4))

    baseline = trials("fixed", 1)
    improved = trials("recursive", 3)
    assert compare_controllers(baseline, improved).approved
    assert not compare_controllers(baseline, trials("recursive", 3, 200)).approved
    assert not compare_controllers(baseline, trials("recursive", 1)).approved


def test_shared_change_cannot_trade_other_domain_regression_for_gain():
    contracts = {d: domain_contract("fixed-" + d, d,
                                    regression_tolerance=0.1)
                 for d in ("coding", "mathematics")}

    def observations(math_score):
        return {d: Observation("fixed-" + d, "shared-candidate", d,
                               (10, 10, 10),
                               (12, 12, 12) if d == "coding" else
                               (math_score,) * 3, DOMAIN_CHECKS[d],
                               source="protected-42", budget_matched=True)
                for d in contracts}

    assert not judge_portfolio(contracts, observations(9.5),
                               frozenset({"coding"})).approved
    assert judge_portfolio(contracts, observations(9.95),
                           frozenset({"coding"})).approved


def test_diagnosis_archives_hypothesis_and_ablation_not_a_promotion(tmp_path):
    store = EvidenceStore(tmp_path / "experiments.jsonl")
    failure = FailureRecord("episode.jsonl", "coding", "wrong dependency edited",
                            "sha256:trace", 12)
    model = FakeBackend(json.dumps({
        "target_component": "fault_localizer",
        "hypothesis": "shared caller hides actual defect",
        "change": "rank dependencies before editing",
        "discriminating_test": "unseen repositories",
        "ablation": "disable dependency ranking",
        "estimated_cost": 10, "expected_gain": 2, "information_value": 3}))
    intervention = diagnose(failure, model, store)
    assert intervention.acquisition() == 0.5
    assert rank_experiments([intervention])[0] == intervention
    assert [r["kind"] for r in store.read()] == ["failure", "intervention_proposed"]


def test_research_worker_corrects_malformed_local_model_response(tmp_path):
    store = EvidenceStore(tmp_path / "retries.jsonl")
    responses = iter(["An informal answer with no JSON",
                      json.dumps({"answer": "n squared", "assumptions": ["n >= 0"],
                                  "verification_plan": "check by induction"})])
    model = FakeBackend(lambda prompt: next(responses))
    problem = ProblemSpec("sum", "mathematics", (), (), (), "formula", 10, "formal-v1")
    result = propose(problem, model, store)
    assert result.status == "hypothesized" and model.calls == 2
    assert [r["kind"] for r in store.read()] == ["proposal_rejected", "proposal"]
