"""M1 exit criterion: paper Alg. 1/2 reproduced on the synthetic suite.

Expectations from paper Table 1 (GATS b=10): 100% success, optimality 1.00,
zero LLM planning calls, zero variance across runs.
"""

import pytest

from ledger.graph import TransitionGraph
from ledger.planner import PlanConfig, run_episode
from ledger.tasks import OPTIMAL_LENGTH, SyntheticEnv, no_backtrack_task, suite
from ledger.world_model import LayeredWorldModel


def run_suite(budget=10):
    out = []
    for task in suite():
        graph = TransitionGraph()
        wm = LayeredWorldModel(task.catalog)
        env = SyntheticEnv(task.catalog)
        out.append(run_episode(task, graph, wm, PlanConfig(budget=budget), env.step))
    return out


@pytest.fixture(scope="module")
def suite_results():
    return run_suite()


def test_full_suite_success(suite_results):
    failures = [
        t.task_id for t, r in zip(suite(), suite_results) if not r.success
    ]
    assert not failures, f"unsolved tasks: {failures}"


def test_full_suite_optimality(suite_results):
    for task, res in zip(suite(), suite_results):
        tier = task.task_id.split("-")[0]
        assert res.steps == OPTIMAL_LENGTH[tier], (
            f"{task.task_id}: {res.steps} steps vs optimal "
            f"{OPTIMAL_LENGTH[tier]}: {res.plan}"
        )


def test_zero_llm_planning_calls(suite_results):
    assert all(r.llm_planning_calls == 0 for r in suite_results)


def test_zero_variance_across_runs(suite_results):
    rerun = run_suite()
    assert [r.plan for r in suite_results] == [r.plan for r in rerun]


def test_no_backtrack_maze_avoids_traps():
    """Locking-door maze (paper stress category): traps are dead ends the
    planner must never enter, since one wrong choice is unrecoverable."""
    task = no_backtrack_task("nb-0", rooms=6)
    graph = TransitionGraph()
    wm = LayeredWorldModel(task.catalog)
    env = SyntheticEnv(task.catalog)
    res = run_episode(task, graph, wm, PlanConfig(), env.step)
    assert res.success
    assert all("Trap" not in a for a in res.plan)
    assert res.steps == 5
    assert graph.dead_ends  # trap successors were provably marked


def test_divergence_feeds_l2_and_recovers():
    """SS6.4: when execution deviates from prediction, the transition is
    recorded into L2 and the episode replans from the observed state."""
    task = suite()[25]  # a medium task
    graph = TransitionGraph()
    wm = LayeredWorldModel(task.catalog)
    inner = SyntheticEnv(task.catalog)

    def noisy_step(s, action_name):
        s2 = inner.step(s, action_name)
        if action_name.endswith("GetResource"):
            return s2 | {"surprise.prop"}
        return s2

    res = run_episode(task, graph, wm, PlanConfig(), noisy_step)
    assert res.success
    assert "surprise.prop" in res.final_props
    assert wm.l2.observations(f"{task.task_id.replace('medium-', 'm')}."
                              f"GetResource") == 1
