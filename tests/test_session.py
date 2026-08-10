"""Multi-agent sessions: deterministic graph merging and real parallel runs."""

import difflib

import pytest
from conftest import BUGGY, FIXED, GOAL, TEST_CMD, needs_git

from ledger.graph import TransitionGraph
from ledger.journal import Journal
from ledger.planner import PlanConfig
from ledger.session import AgentSpec, Session, merge_graphs, slugify

A = frozenset({"a"})
B = frozenset({"b"})
C = frozenset({"c"})


def _graph(edges):
    g = TransitionGraph()
    for u_props, action, v_props, n, q_sum in edges:
        u = g.ensure_node(u_props)
        edge = g.add_edge(u, action, v_props, 1.0, "L1")
        edge.n, edge.q_sum = n, q_sum
    return g


def test_slugify_is_filesystem_safe():
    assert slugify("Fix Auth/Bug #3") == "fix-auth-bug-3"
    assert slugify("!!!") == "agent"


def test_merge_sums_statistics_for_a_shared_edge():
    target = _graph([(A, "Go", B, 2, 4.0)])
    other = _graph([(A, "Go", B, 3, 9.0)])
    stats = merge_graphs(target, [("x", other)])
    edge = target.get_edge(target.ensure_node(A), "Go")
    assert edge.n == 5
    assert edge.q_sum == 13.0
    assert stats["edges_merged"] == 1


def test_merge_adds_unseen_nodes_and_edges():
    target = TransitionGraph()
    other = _graph([(A, "Go", B, 1, 1.0)])
    stats = merge_graphs(target, [("x", other)])
    assert stats["edges_added"] == 1
    assert stats["nodes_added"] == 2


def test_merge_is_order_independent():
    """Agents finish in arbitrary order; the shared graph must not depend on
    it. Merging the same sources in reversed order yields the same result."""
    g1 = _graph([(A, "Go", B, 2, 4.0), (B, "Stop", C, 1, 1.0)])
    g2 = _graph([(A, "Go", C, 5, 10.0)])

    forward = TransitionGraph()
    merge_graphs(forward, [("alpha", g1), ("beta", g2)])
    backward = TransitionGraph()
    merge_graphs(backward, [("beta", _graph([(A, "Go", C, 5, 10.0)])),
                            ("alpha", _graph([(A, "Go", B, 2, 4.0),
                                              (B, "Stop", C, 1, 1.0)]))])

    assert forward.nodes.keys() == backward.nodes.keys()
    assert forward.edges.keys() == backward.edges.keys()
    for key in forward.edges:
        f, b = forward.edges[key], backward.edges[key]
        assert (f.n, f.q_sum, f.v_key) == (b.n, b.q_sum, b.v_key)


def test_merge_prefers_the_better_supported_prediction():
    target = _graph([(A, "Go", B, 1, 1.0)])
    other = _graph([(A, "Go", C, 9, 9.0)])
    merge_graphs(target, [("x", other)])
    edge = target.get_edge(target.ensure_node(A), "Go")
    assert edge.v_key == target.ensure_node(C)  # 9 visits beat 1
    assert edge.n == 10


def test_merge_unions_dead_ends():
    target = TransitionGraph()
    other = TransitionGraph()
    other.ensure_node(A)
    other.mark_dead_end(other.ensure_node(A))
    stats = merge_graphs(target, [("x", other)])
    assert stats["dead_ends_added"] == 1
    assert target.is_dead_end(target.ensure_node(A))


def test_duplicate_agent_names_are_rejected(tmp_path):
    session = Session(tmp_path / "s")
    spec = AgentSpec("dup", tmp_path, TEST_CMD, goal=GOAL)
    other = AgentSpec("dup", tmp_path, TEST_CMD, goal=GOAL)
    with pytest.raises(ValueError):
        session.run_parallel([spec, other])


def test_names_colliding_after_slugify_are_rejected(tmp_path):
    session = Session(tmp_path / "s")
    specs = [AgentSpec("fix bug", tmp_path, TEST_CMD, goal=GOAL),
             AgentSpec("fix/bug", tmp_path, TEST_CMD, goal=GOAL)]
    with pytest.raises(ValueError):
        session.run_parallel(specs)


def test_agent_spec_requires_a_target(tmp_path):
    with pytest.raises(ValueError):
        AgentSpec("x", tmp_path, TEST_CMD)


def test_empty_session_is_a_no_op(tmp_path):
    assert Session(tmp_path / "s").run_parallel([]) == []


def _queue_fix_for(session, agent_name):
    queue = session.dir / "agents" / slugify(agent_name) / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    diff = "".join(difflib.unified_diff(
        BUGGY.splitlines(keepends=True), FIXED.splitlines(keepends=True),
        fromfile="a/calc.py", tofile="b/calc.py"))
    (queue / "fix-001.patch").write_text(diff, encoding="utf-8")


@needs_git
def test_two_agents_run_in_parallel_and_pool_their_graphs(repo, tmp_path):
    session = Session(tmp_path / "sess", cfg=PlanConfig(budget=6))
    specs = [AgentSpec(name, repo, TEST_CMD, goal=GOAL)
             for name in ("alpha", "beta")]
    for spec in specs:
        _queue_fix_for(session, spec.name)

    outcomes = session.run_parallel(specs, max_workers=2)

    assert [o.name for o in outcomes] == ["alpha", "beta"]  # sorted, not timing
    assert all(o.success for o in outcomes), [o.error for o in outcomes]
    # Isolation: separate worktrees, separate journals, separate branches.
    assert outcomes[0].journal_path != outcomes[1].journal_path
    for o in outcomes:
        ok, err = Journal.verify_chain(str(o.journal_path))
        assert ok, err

    # Pooling: the shared graph exists and carries both agents' structure.
    shared = session.shared_stats()
    assert shared["nodes"] > 0 and shared["edges"] > 0

    entries = Journal.read(str(session.journal_path))
    kinds = [e["t"] for e in entries]
    assert kinds[0] == "SESSION_START" and kinds[-1] == "SESSION_END"
    merge = next(e for e in entries if e["t"] == "MERGE")
    assert merge["agents"] == ["alpha", "beta"]  # deterministic merge order
    ok, err = Journal.verify_chain(str(session.journal_path))
    assert ok, err


@needs_git
def test_a_failing_agent_does_not_sink_the_session(repo, tmp_path):
    """One agent has no patch queued and cannot reach its goal; the other
    still completes, and the session reports both honestly."""
    session = Session(tmp_path / "sess2", cfg=PlanConfig(budget=6, max_steps=4))
    specs = [AgentSpec(name, repo, TEST_CMD, goal=GOAL)
             for name in ("works", "stuck")]
    _queue_fix_for(session, "works")

    outcomes = session.run_parallel(specs, max_workers=2)
    by_name = {o.name: o for o in outcomes}
    assert by_name["works"].success
    assert not by_name["stuck"].success

    entries = Journal.read(str(session.journal_path))
    end = next(e for e in entries if e["t"] == "SESSION_END")
    assert end["succeeded"] == ["works"]
    assert end["failed"] == ["stuck"]
