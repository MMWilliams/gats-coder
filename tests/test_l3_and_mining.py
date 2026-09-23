"""M3 exit criterion: L3 bootstraps once and falls to zero (paper Table 4 /
Eq. 9), and journal mining feeds L2.

Domain: a chain with a Mystery action whose preconditions are known but
whose effects aren't (the paper's 'novel action' case). The environment
knows the truth; the planner must learn it via L3, then never pay for it
again.
"""

import json

from ledger.actions import Action, Catalog
from ledger.graph import TransitionGraph
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.planner import PlanConfig, Task, run_episode
from ledger.tasks import SyntheticEnv
from ledger.world_model import (L2Learned, L3Generative, LayeredWorldModel,
                                mine_journal)


def planner_catalog() -> Catalog:
    return Catalog([
        Action("Step1", prec=frozenset({"start"}), add=frozenset({"s1"})),
        Action("Mystery", prec=frozenset({"s1"}), effects_known=False),
        Action("Finish", prec=frozenset({"s2"}), add=frozenset({"goal"})),
    ])


def truth_catalog() -> Catalog:
    return Catalog([
        Action("Step1", prec=frozenset({"start"}), add=frozenset({"s1"})),
        Action("Mystery", prec=frozenset({"s1"}), add=frozenset({"s2"})),
        Action("Finish", prec=frozenset({"s2"}), add=frozenset({"goal"})),
    ])


def oracle(prompt: str) -> str:
    """A backend that knows the domain (the paper's L3 assumption)."""
    action = next(line.split("Action: ", 1)[1]
                  for line in prompt.splitlines() if line.startswith("Action: "))
    if action == "Mystery":
        return json.dumps({"add": ["s2"], "del": []})
    return json.dumps({"add": [], "del": []})


def make_task() -> Task:
    return Task("l3-demo", frozenset({"start"}), frozenset({"goal"}),
                planner_catalog())


def run(wm, journal=None):
    env = SyntheticEnv(truth_catalog())
    return run_episode(make_task(), TransitionGraph(), wm, PlanConfig(),
                       env.step, journal=journal)


def test_l3_bootstraps_then_cache_drops_calls_to_zero(tmp_path):
    # Episode 1: bootstrapping -- the LLM is consulted for the novel action.
    backend1 = FakeBackend(oracle)
    l3 = L3Generative(backend1)
    jpath = str(tmp_path / "ep1.jsonl")
    with Journal(jpath) as j:
        res1 = run(LayeredWorldModel(planner_catalog(), l3=l3), journal=j)
    assert res1.success
    assert res1.plan == ["Step1", "Mystery", "Finish"]
    assert res1.llm_planning_calls > 0  # one-time bootstrap (paper Table 4)
    cache_path = str(tmp_path / "l3.sqlite")
    l3.save(cache_path)

    # Episode 2: same domain, fresh model, warm cache -- zero LLM calls.
    backend2 = FakeBackend(oracle)
    l3b = L3Generative(backend2, cache_path=cache_path)
    res2 = run(LayeredWorldModel(planner_catalog(), l3=l3b))
    assert res2.success and res2.plan == res1.plan
    assert res2.llm_planning_calls == 0
    assert backend2.calls == 0  # Eq. 9: bounded by distinct novel queries

    # Episode 3: no cache, but L2 mined from episode 1's journal covers the
    # executed path -- calls fall (only unexecuted exploratory queries remain).
    l2 = L2Learned()
    assert mine_journal(jpath, l2) == res1.steps
    backend3 = FakeBackend(oracle)
    wm3 = LayeredWorldModel(planner_catalog(), l3=L3Generative(backend3))
    wm3.l2 = l2
    res3 = run(wm3)
    assert res3.success and res3.plan == res1.plan
    assert res3.llm_planning_calls < res1.llm_planning_calls


def test_l2_persistence_roundtrip(tmp_path):
    l2 = L2Learned()
    s = frozenset({"a", "b"})
    l2.record(s, "Act", frozenset({"a", "b", "c"}))
    l2.record(s, "Act", frozenset({"a", "b", "c"}))
    path = str(tmp_path / "l2.sqlite")
    l2.save(path)

    l2b = L2Learned()
    l2b.load(path)
    assert l2b.observations_at(s, "Act") == 2
    pred = l2b.predict(s, "Act")
    assert pred.next_props == frozenset({"a", "b", "c"})
    assert pred.p == 0.2  # min(2/10, 1)


def test_mined_prediction_carries_l2_provenance(tmp_path):
    backend = FakeBackend(oracle)
    jpath = str(tmp_path / "ep.jsonl")
    with Journal(jpath) as j:
        run(LayeredWorldModel(planner_catalog(),
                              l3=L3Generative(backend)), journal=j)
    l2 = L2Learned()
    mine_journal(jpath, l2)
    wm = LayeredWorldModel(planner_catalog())
    wm.l2 = l2
    pred = wm.predict(frozenset({"start", "s1"}), "Mystery")
    assert pred.layer == "L2"
    assert "s2" in pred.next_props


def test_l3_cache_ignores_bookkeeping():
    """A later round adds patch.applied:iterationN to every state. That
    changes nothing about what an action does, so it must not buy a fresh
    model call: the chess loop spent ~45% of six hours re-asking."""
    backend = FakeBackend(oracle)
    wm = LayeredWorldModel(planner_catalog(), l3=L3Generative(backend))
    base = frozenset({"s1"})
    first = wm.predict(base | {"patch.applied:iteration1"}, "Mystery")
    again = wm.predict(base | {"patch.applied:iteration2",
                               "patch.queued:iteration2",
                               "repo.branch:ledger/x"}, "Mystery")
    assert backend.calls == 1
    assert "s2" in first.next_props and "s2" in again.next_props
    # the asking state's own bookkeeping survives in the prediction
    assert "patch.applied:iteration2" in again.next_props


def test_l3_drops_propositions_no_sensor_reports():
    """The live model answered ReviewDesign with `design.reviewed`, a prop
    nothing can observe. Unobservable predictions become an honest no-op."""
    backend = FakeBackend(lambda _: json.dumps(
        {"add": ["s2", "design.reviewed"], "del": ["made.up"]}))
    wm = LayeredWorldModel(planner_catalog(), l3=L3Generative(backend))
    pred = wm.predict(frozenset({"s1", "made.up"}), "Mystery")
    assert pred.next_props == frozenset({"s1", "s2", "made.up"})
