"""Pooled graph seeding: does agent N+1 actually inherit anything?

The mechanism only pays off if state keys MATCH across agents. DESIGN.md
SS12.8 warns that `repo.branch:ledger/<episode>` makes every episode's keys
unique, which would make a seeded graph inert -- present, but never hit.
These tests measure that directly rather than assuming either way.
"""

import difflib

from conftest import BUGGY, FIXED, GOAL, TEST_CMD, needs_git

from ledger.graph import TransitionGraph
from ledger.journal import Journal
from ledger.planner import PlanConfig
from ledger.session import AgentSpec, Session, slugify


def _queue_fix(session, agent_name):
    queue = session.dir / "agents" / slugify(agent_name) / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    diff = "".join(difflib.unified_diff(
        BUGGY.splitlines(keepends=True), FIXED.splitlines(keepends=True),
        fromfile="a/calc.py", tofile="b/calc.py"))
    (queue / "fix-001.patch").write_text(diff, encoding="utf-8")


@needs_git
def test_second_agent_is_seeded_from_the_shared_graph(repo, tmp_path):
    """The loop closes: run one agent, then a second, and the second's
    journal HEADER should record a non-empty seeded graph."""
    session = Session(tmp_path / "s", cfg=PlanConfig(budget=6))

    first = AgentSpec("alpha", repo, TEST_CMD, goal=GOAL)
    _queue_fix(session, "alpha")
    session.run_parallel([first])
    assert session.graph_path.exists()
    pooled = TransitionGraph.load(str(session.graph_path)).stats()
    assert pooled["edges"] > 0, "first agent contributed nothing to pool"

    second = AgentSpec("beta", repo, TEST_CMD, goal=GOAL)
    _queue_fix(session, "beta")
    outcomes = session.run_parallel([second])

    header = Journal.read(str(outcomes[0].journal_path))[0]
    seeded = header.get("seeded_graph")
    assert seeded is not None, "HEADER does not record the seed"
    assert seeded["edges"] > 0, (
        "second agent started from an EMPTY graph -- the pooling loop is "
        f"still open (pool had {pooled['edges']} edges)")


@needs_git
def test_cold_arm_really_is_cold(repo, tmp_path):
    """share_graph=False is the control: the pool still fills, but no agent
    starts from it. Without a credible cold arm there is no experiment."""
    session = Session(tmp_path / "cold", cfg=PlanConfig(budget=6),
                      share_graph=False)
    for name in ("alpha", "beta"):
        _queue_fix(session, name)
        session.run_parallel([AgentSpec(name, repo, TEST_CMD, goal=GOAL)])

    beta_journal = session.dir / "agents" / "beta" / "episode.jsonl"
    header = Journal.read(str(beta_journal))[0]
    assert header["seeded_graph"]["edges"] == 0, "cold arm was seeded"
    # ...but the pool still accumulated, so the only difference is the seed.
    assert TransitionGraph.load(str(session.graph_path)).stats()["edges"] > 0


@needs_git
def test_state_keys_overlap_across_agents(repo, tmp_path):
    """The SS12.8 risk, measured. If agent-generated identifiers make every
    key unique, the seeded graph is inert and the whole amortization claim
    collapses. This test reports the actual overlap."""
    session = Session(tmp_path / "overlap", cfg=PlanConfig(budget=6),
                      share_graph=False)
    graphs = {}
    for name in ("alpha", "beta"):
        _queue_fix(session, name)
        session.run_parallel([AgentSpec(name, repo, TEST_CMD, goal=GOAL)])
        g = TransitionGraph.load(
            str(session.dir / "agents" / name / "graph.sqlite"))
        graphs[name] = set(g.nodes)

    a, b = graphs["alpha"], graphs["beta"]
    shared = a & b
    print(f"\nalpha nodes={len(a)}  beta nodes={len(b)}  shared={len(shared)}")
    assert shared, (
        "ZERO state-key overlap between two agents on the SAME repo running "
        "the SAME goal. Agent-generated identifiers (DESIGN.md SS12.8, "
        "repo.branch:ledger/<episode>) are almost certainly in k(s). A "
        "seeded graph can never be hit until those are stripped.")
