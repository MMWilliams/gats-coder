"""Journal chain + bit-exact replay (M1 exit criterion), incl. tamper evidence."""

from ledger.graph import TransitionGraph
from ledger.journal import Journal
from ledger.planner import PlanConfig, run_episode
from ledger.replay import replay
from ledger.tasks import SyntheticEnv, medium_task
from ledger.world_model import LayeredWorldModel


def make_journaled_episode(tmp_path):
    task = medium_task("medium-00", p="m00.")
    jpath = str(tmp_path / "episode.jsonl")
    with Journal(jpath) as journal:
        graph = TransitionGraph()
        wm = LayeredWorldModel(task.catalog)
        env = SyntheticEnv(task.catalog)
        res = run_episode(task, graph, wm, PlanConfig(), env.step, journal=journal)
    return jpath, res


def test_chain_verifies(tmp_path):
    jpath, res = make_journaled_episode(tmp_path)
    assert res.success
    ok, err = Journal.verify_chain(jpath)
    assert ok, err


def test_replay_bit_exact(tmp_path):
    jpath, _ = make_journaled_episode(tmp_path)
    result = replay(jpath)
    assert result.ok, result.mismatches
    assert result.steps_compared == 5


def test_tamper_breaks_chain_and_replay(tmp_path):
    jpath, _ = make_journaled_episode(tmp_path)
    with open(jpath, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    # Entry 1 is the first PLAN entry; nudge one recorded value.
    assert '"search_iters":10' in lines[1]
    lines[1] = lines[1].replace('"search_iters":10', '"search_iters":11')
    with open(jpath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")

    ok, err = Journal.verify_chain(jpath)
    assert not ok
    assert "entry 1" in err
    assert not replay(jpath).ok
