"""Transposition merging (paper Eq. 12) and SQLite persistence."""

from ledger.graph import TransitionGraph


def test_transposition_merging():
    g = TransitionGraph()
    start_a = frozenset({"a"})
    start_b = frozenset({"b"})
    shared = frozenset({"x", "y"})

    ka = g.ensure_node(start_a)
    kb = g.ensure_node(start_b)
    e1 = g.add_edge(ka, "go", shared, 1.0, "L1")
    e2 = g.add_edge(kb, "go", shared, 1.0, "L1")

    # Two different paths reach the same proposition set -> one node.
    assert e1.v_key == e2.v_key
    assert len(g.nodes) == 3


def test_save_load_roundtrip(tmp_path):
    g = TransitionGraph()
    empty = frozenset()  # easy tasks start from the empty state
    k0 = g.ensure_node(empty)
    k1 = g.ensure_node(frozenset({"a1", "a2"}))
    g.add_edge(k0, "StartA", frozenset({"a1", "a2"}), 1.0, "L1")
    g.update_stats(k0, "StartA", 3.25)
    g.update_stats(k0, "StartA", 1.75)
    g.mark_dead_end(k1)

    path = str(tmp_path / "graph.sqlite")
    g.save(path)
    g2 = TransitionGraph.load(path)

    assert g2.stats() == g.stats()
    assert g2.props_of(k0) == frozenset()  # empty-state round-trip
    edge = g2.get_edge(k0, "StartA")
    assert edge.n == 2 and edge.q == 2.5 and edge.layer == "L1"
    assert g2.is_dead_end(k1)
