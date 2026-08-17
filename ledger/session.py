"""Multi-agent sessions: several agents, one shared transition graph.

DESIGN.md SS1 lists multi-agent as a v1 non-goal ("one episode = one repo =
one goal"). This module lifts that restriction along the axis GATS actually
supports: agents work *concurrently* in isolated git worktrees, and the
verified edges they discover are pooled into one persistent graph
(paper SS3.3 -- persistence amortizes prediction, value estimation and UCB
statistics).

Two properties are preserved deliberately:

* **Isolation.** Each agent gets its own worktree, branch, episode directory
  and journal. Nothing an agent does can corrupt a sibling's state; the
  shared artifact is the graph, and it is merged only after every agent has
  finished.
* **Determinism.** Agents finish in arbitrary wall-clock order, but the merge
  is applied in sorted-agent-name order, so the resulting shared graph is a
  pure function of the per-agent results. The merge order is journaled.

What is *not* shared yet: L2 statistics are seeded read-only from the session
store and are not merged back (per-state counts from different repos are not
safely additive without the state-abstraction fix noted in DESIGN.md SS12.8).
Agents therefore share structure, not yet learned dynamics.
"""

from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .executor import run_repo_episode
from .graph import EdgeStat, TransitionGraph
from .journal import Journal
from .planner import PlanConfig
from .state import Props

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def slugify(name: str) -> str:
    slug = _SAFE.sub("-", name).strip("-").lower()
    return slug or "agent"


@dataclass
class AgentSpec:
    """One unit of parallel work."""

    name: str
    repo_root: Path
    test_cmd: list[str]
    task_text: str | None = None
    goal: Props | None = None
    slots: list[str] | None = None
    context_files: list[str] = field(default_factory=list)
    # Per-agent backend override (secondary-model economics: bind cheap
    # models to fan-out work by declaration). None -> the session backend.
    backend: object | None = None

    def __post_init__(self):
        if self.task_text is None and self.goal is None:
            raise ValueError(f"agent {self.name!r}: need task_text or goal")


@dataclass
class AgentOutcome:
    name: str
    success: bool
    plan: list[str]
    steps: int
    incidents: int
    llm_planning_calls: int
    journal_path: Path | None
    graph_path: Path | None
    error: str | None = None


def merge_graphs(target: TransitionGraph,
                 sources: list[tuple[str, TransitionGraph]]) -> dict:
    """Fold agent graphs into `target` deterministically.

    `sources` is a list of (agent_name, graph); it is sorted by name before
    merging so the result never depends on completion order.

    Node keys are content hashes of the proposition set, so identical keys
    carry identical props and union is safe. For an edge present in more than
    one graph, UCB statistics are summed (they are independent samples of the
    same arm) and the *prediction* is taken from the best-supported observer:
    highest visit count, ties broken on the successor key lexicographically.
    """
    stats = {"agents": [], "nodes_added": 0, "edges_added": 0,
             "edges_merged": 0, "dead_ends_added": 0}
    for name, g in sorted(sources, key=lambda pair: pair[0]):
        stats["agents"].append(name)
        for key, props in sorted(g.nodes.items()):
            if key not in target.nodes:
                target.nodes[key] = props
                stats["nodes_added"] += 1
        for key in sorted(g.dead_ends):
            if key not in target.dead_ends:
                target.dead_ends.add(key)
                stats["dead_ends_added"] += 1
        for (u, action), edge in sorted(g.edges.items()):
            existing = target.edges.get((u, action))
            if existing is None:
                target.edges[(u, action)] = EdgeStat(
                    v_key=edge.v_key, p=edge.p, layer=edge.layer,
                    n=edge.n, q_sum=edge.q_sum)
                stats["edges_added"] += 1
                continue
            # Prefer the better-supported prediction, deterministically.
            if (edge.n, existing.v_key) > (existing.n, edge.v_key):
                existing.v_key, existing.p, existing.layer = (
                    edge.v_key, edge.p, edge.layer)
            existing.n += edge.n
            existing.q_sum += edge.q_sum
            stats["edges_merged"] += 1
    return stats


class Session:
    """A working session that can run many agents and pool what they learn."""

    def __init__(self, session_dir: Path | str, cfg: PlanConfig | None = None,
                 backend=None, gate=None, profile: str = "attended",
                 max_author_attempts: int = 6, hooks: list[dict] | None = None,
                 share_graph: bool = True):
        self.dir = Path(session_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg or PlanConfig()
        self.backend = backend
        # share_graph=False is the COLD control arm: agents still merge their
        # graphs into the shared store, but none of them start from it. That
        # isolates "does pooled structure help" from every other variable.
        self.share_graph = share_graph
        from .executor import attended_gate
        self.gate = gate or attended_gate
        self.profile = profile
        self.max_author_attempts = max_author_attempts
        self.hooks = hooks
        self.graph_path = self.dir / "shared-graph.sqlite"
        self.l2_path = self.dir / "shared-l2.sqlite"
        self.l3_path = self.dir / "shared-l3.sqlite"
        self.journal_path = self.dir / "session.jsonl"

    # -- single agent --------------------------------------------------------
    def _run_one(self, spec: AgentSpec) -> AgentOutcome:
        episode_dir = self.dir / "agents" / slugify(spec.name)
        episode_dir.mkdir(parents=True, exist_ok=True)
        # Seed learned layers read-only: agents benefit from prior episodes,
        # but write to private copies so concurrent runs cannot race.
        private_l2 = episode_dir / "l2.sqlite"
        if self.l2_path.exists() and not private_l2.exists():
            shutil.copyfile(self.l2_path, private_l2)
        private_l3 = episode_dir / "l3.sqlite"
        if self.l3_path.exists() and not private_l3.exists():
            shutil.copyfile(self.l3_path, private_l3)
        try:
            result, executor, journal_path = run_repo_episode(
                spec.repo_root, episode_dir, spec.goal, spec.test_cmd,
                cfg=self.cfg, task_text=spec.task_text,
                backend=spec.backend or self.backend,
                l2_path=str(private_l2), l3_cache_path=str(private_l3),
                slots=spec.slots, context_files=spec.context_files,
                gate=self.gate, profile=self.profile,
                max_author_attempts=self.max_author_attempts,
                hooks=self.hooks,
                # Close the pooling loop: agents merge INTO the shared graph
                # after running, and now also start FROM it. Without this the
                # pooled graph was write-only and a population learned nothing
                # from itself.
                seed_graph_path=(str(self.graph_path)
                                 if self.share_graph and self.graph_path.exists()
                                 else None),
            )
        except Exception as exc:
            return AgentOutcome(spec.name, False, [], 0, 0, 0, None, None,
                                error=f"{type(exc).__name__}: {exc}")
        return AgentOutcome(
            name=spec.name, success=result.success, plan=list(result.plan),
            steps=result.steps, incidents=executor.incidents,
            llm_planning_calls=result.llm_planning_calls,
            journal_path=journal_path,
            graph_path=episode_dir / "graph.sqlite",
        )

    # -- fan-out -------------------------------------------------------------
    def run_parallel(self, specs: list[AgentSpec],
                     max_workers: int = 4) -> list[AgentOutcome]:
        """Run every spec concurrently, then merge their graphs in name order.

        Returns outcomes sorted by agent name (not completion order), so
        callers and tests see a stable sequence.
        """
        if not specs:
            return []
        names = [s.name for s in specs]
        if len(names) != len(set(names)):
            raise ValueError("agent names must be unique within a session")
        if len({slugify(n) for n in names}) != len(names):
            raise ValueError("agent names must remain unique after slugify")

        with Journal(str(self.journal_path)) as journal:
            journal.append("SESSION_START", agents=sorted(names),
                           workers=max_workers, config=self.cfg.to_dict())
            outcomes: list[AgentOutcome] = []
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(self._run_one, s): s for s in specs}
                for fut in as_completed(futures):
                    outcome = fut.result()
                    outcomes.append(outcome)
                    journal.append(
                        "AGENT_DONE", agent=outcome.name,
                        success=outcome.success, steps=outcome.steps,
                        incidents=outcome.incidents, plan=outcome.plan,
                        error=outcome.error,
                    )

            shared = (TransitionGraph.load(str(self.graph_path))
                      if self.graph_path.exists() else TransitionGraph())
            sources = []
            for outcome in outcomes:
                if outcome.graph_path and outcome.graph_path.exists():
                    sources.append(
                        (outcome.name, TransitionGraph.load(str(outcome.graph_path))))
            stats = merge_graphs(shared, sources)
            shared.save(str(self.graph_path))
            journal.append("MERGE", **stats, shared=shared.stats())
            journal.append(
                "SESSION_END",
                succeeded=sorted(o.name for o in outcomes if o.success),
                failed=sorted(o.name for o in outcomes if not o.success),
            )
        return sorted(outcomes, key=lambda o: o.name)

    def shared_stats(self) -> dict:
        if not self.graph_path.exists():
            return {"nodes": 0, "edges": 0, "dead_ends": 0, "edges_by_layer": {}}
        return TransitionGraph.load(str(self.graph_path)).stats()
