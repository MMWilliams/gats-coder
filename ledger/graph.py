"""Persistent state-transition graph G = (V, E) per paper SS3.3 (Eqs. 10-12).

Nodes are canonical state keys (transposition table: paths reaching the same
proposition set merge). Edges store the action, predicted successor, world-
model confidence and layer, plus UCB search statistics (N visits, Q sum).
The graph persists across planning steps and across episodes (SQLite).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .state import Props, state_key


@dataclass
class EdgeStat:
    v_key: str
    p: float
    layer: str
    n: int = 0
    q_sum: float = 0.0

    @property
    def q(self) -> float:
        return self.q_sum / self.n if self.n else 0.0


class TransitionGraph:
    def __init__(self):
        self.nodes: dict[str, Props] = {}
        self.edges: dict[tuple[str, str], EdgeStat] = {}
        self.dead_ends: set[str] = set()

    # -- nodes ---------------------------------------------------------------
    def ensure_node(self, props: Props) -> str:
        k = state_key(props)
        if k not in self.nodes:
            self.nodes[k] = props
        return k

    def props_of(self, key: str) -> Props:
        return self.nodes[key]

    # -- edges ---------------------------------------------------------------
    def get_edge(self, u_key: str, action_name: str) -> EdgeStat | None:
        return self.edges.get((u_key, action_name))

    def add_edge(self, u_key: str, action_name: str, v_props: Props,
                 p: float, layer: str) -> EdgeStat:
        v_key = self.ensure_node(v_props)
        edge = EdgeStat(v_key=v_key, p=p, layer=layer)
        self.edges[(u_key, action_name)] = edge
        return edge

    def update_stats(self, u_key: str, action_name: str, value: float) -> None:
        edge = self.edges[(u_key, action_name)]
        edge.n += 1
        edge.q_sum += value

    def out_edges(self, u_key: str) -> dict[str, EdgeStat]:
        return {a: e for (u, a), e in self.edges.items() if u == u_key}

    # -- dead ends -----------------------------------------------------------
    def mark_dead_end(self, key: str) -> None:
        self.dead_ends.add(key)

    def is_dead_end(self, key: str) -> bool:
        return key in self.dead_ends

    # -- persistence ---------------------------------------------------------
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS nodes (
        key TEXT PRIMARY KEY, props TEXT NOT NULL, dead_end INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS edges (
        u TEXT NOT NULL, action TEXT NOT NULL, v TEXT NOT NULL,
        p REAL NOT NULL, layer TEXT NOT NULL,
        n INTEGER NOT NULL, q_sum REAL NOT NULL,
        PRIMARY KEY (u, action)
    );
    """

    def save(self, path: str) -> None:
        con = sqlite3.connect(path)
        try:
            con.executescript(self._SCHEMA)
            con.execute("DELETE FROM nodes")
            con.execute("DELETE FROM edges")
            con.executemany(
                "INSERT INTO nodes VALUES (?,?,?)",
                [(k, "\n".join(sorted(props)), int(k in self.dead_ends))
                 for k, props in self.nodes.items()],
            )
            con.executemany(
                "INSERT INTO edges VALUES (?,?,?,?,?,?,?)",
                [(u, a, e.v_key, e.p, e.layer, e.n, e.q_sum)
                 for (u, a), e in self.edges.items()],
            )
            con.commit()
        finally:
            con.close()

    @staticmethod
    def load(path: str) -> "TransitionGraph":
        g = TransitionGraph()
        con = sqlite3.connect(path)
        try:
            con.executescript(TransitionGraph._SCHEMA)
            for key, props, dead in con.execute("SELECT * FROM nodes"):
                g.nodes[key] = frozenset(props.split("\n")) if props else frozenset()
                if dead:
                    g.dead_ends.add(key)
            for u, a, v, p, layer, n, q_sum in con.execute("SELECT * FROM edges"):
                g.edges[(u, a)] = EdgeStat(v_key=v, p=p, layer=layer, n=n, q_sum=q_sum)
        finally:
            con.close()
        return g

    def stats(self) -> dict:
        by_layer: dict[str, int] = {}
        for e in self.edges.values():
            by_layer[e.layer] = by_layer.get(e.layer, 0) + 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "dead_ends": len(self.dead_ends),
            "edges_by_layer": by_layer,
        }
