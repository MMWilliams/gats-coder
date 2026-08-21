"""GATS planner: Alg. 1 (UCB1 search over graph edges) + Alg. 2 (episode loop)
+ BFS StateValue (Eq. 18), ported per paper SS3.4-3.5.

Fully deterministic: no RNG anywhere. Ties in UCB/Q argmax break
lexicographically by action name; BFS expands actions in name order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable

from . import AGENT_VERSION
from .actions import Catalog
from .graph import TransitionGraph
from .journal import Journal
from .state import Props, canonical, state_key
from .world_model import LayeredWorldModel


@dataclass(frozen=True)
class PlanConfig:
    budget: int = 10          # b  (paper Table 2: saturates at 10)
    c: float = 1.0            # UCB exploration constant (paper Table 7)
    lam: float = 1.0          # lambda penalty on uncertain predictions (Eq. 17)
    alpha: float = 10.0       # StateValue scale (paper: alpha = 10)
    value_depth: int = 20     # BFS depth cap (paper SS6: d = 20)
    value_max_states: int = 500  # BFS state cap (paper SS6: max 500)
    max_steps: int = 20       # plan-length cap (paper SS4.4)

    def to_dict(self) -> dict:
        return {
            "budget": self.budget, "c": self.c, "lam": self.lam,
            "alpha": self.alpha, "value_depth": self.value_depth,
            "value_max_states": self.value_max_states, "max_steps": self.max_steps,
        }

    @staticmethod
    def from_dict(d: dict) -> "PlanConfig":
        return PlanConfig(**d)


@dataclass(frozen=True)
class Task:
    task_id: str
    initial: Props
    goal: Props
    catalog: Catalog


@dataclass
class EpisodeResult:
    success: bool
    plan: list[str]
    final_props: Props
    steps: int
    llm_planning_calls: int
    plan_records: list[dict] = field(default_factory=list)


def state_value(props: Props, goal: Props, catalog: Catalog,
                wm: LayeredWorldModel, cfg: PlanConfig,
                graph: TransitionGraph, cache: dict) -> float:
    """Eq. 18: alpha / (d_G(s, G) + 1) if a goal is reachable, else 0.

    BFS through world-model predictions, capped by value_depth and
    value_max_states; a cap-limited miss is treated as unreachable (heuristic,
    per paper SS6 -- exact on the small spaces M1 targets). Unreachable states
    are marked dead ends in the persistent graph (SS3.3).

    Applicability comes from catalog contracts; when L2/L3 later supply
    actions without contracts, applicability must come from learned precs (M3).
    """
    key = (state_key(props), canonical(goal))
    if key in cache:
        return cache[key]

    if goal <= props:
        cache[key] = cfg.alpha  # d = 0
        return cfg.alpha

    seen = {state_key(props)}
    frontier = [props]
    depth = 0
    value = 0.0
    found = False
    while frontier and depth < cfg.value_depth and len(seen) <= cfg.value_max_states:
        depth += 1
        nxt = []
        for s in frontier:
            for a in catalog.applicable(s):
                pred = wm.predict(s, a.name)
                if pred.p <= 0.0:
                    continue
                sk = state_key(pred.next_props)
                if sk in seen:
                    continue
                seen.add(sk)
                if goal <= pred.next_props:
                    value = cfg.alpha / (depth + 1)
                    found = True
                    break
                nxt.append(pred.next_props)
            if found:
                break
        if found:
            break
        frontier = nxt

    if not found and value == 0.0:
        graph.mark_dead_end(state_key(props))
    cache[key] = value
    return value


def _argmax_action(scores: dict[str, float]) -> str:
    """Deterministic argmax: highest score, ties -> lexicographically first."""
    best_name, best = None, -math.inf
    for name in sorted(scores):
        if scores[name] > best:
            best_name, best = name, scores[name]
    return best_name


def useful_actions(props: Props, catalog: Catalog, graph: TransitionGraph,
                   wm: LayeredWorldModel) -> list:
    """A_app(s) minus predicted no-ops.

    An action whose predicted successor is the current state cannot lie on a
    shortest path to the goal, but under Eq. 17 a deterministic no-op scores
    1.0*StateValue(s) -- which already prices in the optimal continuation --
    and thereby starves uncertain-but-necessary edges (p<1). The paper's
    fully symbolic setting (p=1 everywhere) never exhibits this; any open
    domain does. Pruning predicted self-loops restores the shortest-path
    argument. Note the corollary: an action NO world-model layer can predict
    (pred = no-change, p=0) is also pruned -- GATS cannot plan through an
    action it cannot model.
    """
    u = graph.ensure_node(props)
    useful = []
    for a in catalog.applicable(props):
        edge = graph.get_edge(u, a.name)
        nxt = graph.props_of(edge.v_key) if edge is not None \
            else wm.predict(props, a.name).next_props
        if state_key(nxt) == u:
            continue
        useful.append(a)
    return useful


def gats_search(props: Props, goal: Props, task: Task, graph: TransitionGraph,
                wm: LayeredWorldModel, cfg: PlanConfig,
                value_cache: dict, a_app: list) -> tuple[str, list[dict]]:
    """Paper Alg. 1: UCB1 selection over graph edges with explicit graph memory.

    Returns (chosen action, journal-ready UCB table covering every arm).
    """
    catalog = task.catalog
    u = graph.ensure_node(props)
    if not a_app:
        raise ValueError("gats_search called with empty A_app")

    for _ in range(cfg.budget):
        # UCB over arms (Eq. 14-16); unvisited arms score +inf.
        n_total = sum(
            (graph.get_edge(u, a.name).n if graph.get_edge(u, a.name) else 0)
            for a in a_app
        )
        ucb: dict[str, float] = {}
        for a in a_app:
            edge = graph.get_edge(u, a.name)
            if edge is None or edge.n == 0:
                ucb[a.name] = math.inf
            else:
                ucb[a.name] = edge.q + cfg.c * math.sqrt(
                    2.0 * math.log(n_total) / edge.n
                )
        chosen = _argmax_action(ucb)

        edge = graph.get_edge(u, chosen)
        if edge is None:
            pred = wm.predict(props, chosen)
            edge = graph.add_edge(u, chosen, pred.next_props, pred.p, pred.layer)
        v_props = graph.props_of(edge.v_key)
        # Eq. 17: v = p * StateValue(s') - lambda * (1 - p)
        v = edge.p * state_value(
            v_props, goal, catalog, wm, cfg, graph, value_cache
        ) - cfg.lam * (1.0 - edge.p)
        graph.update_stats(u, chosen, v)

    q_scores = {a.name: graph.get_edge(u, a.name).q
                for a in a_app if graph.get_edge(u, a.name) is not None
                and graph.get_edge(u, a.name).n > 0}
    chosen = _argmax_action(q_scores)

    table = []
    for a in a_app:
        edge = graph.get_edge(u, a.name)
        if edge is None:
            table.append({"a": a.name, "n": 0, "q": 0.0, "p": None,
                          "layer": None, "v_key": None})
        else:
            table.append({"a": a.name, "n": edge.n, "q": edge.q, "p": edge.p,
                          "layer": edge.layer, "v_key": edge.v_key})
    return chosen, table


# env_step(props, action_name) -> observed next Props
EnvStep = Callable[[Props, str], Props]


def run_episode(task: Task, graph: TransitionGraph, wm: LayeredWorldModel,
                cfg: PlanConfig, env_step: EnvStep,
                journal: Journal | None = None,
                header_extra: dict | None = None,
                strategist: Callable | None = None) -> EpisodeResult:
    """Paper Alg. 2 with the verify-after-execute divergence arm (SS6.4).

    llm_planning_calls counts L3 invocations made during search -- the
    quantity the paper drives to zero; skill-level calls are separate (M2+).
    """
    if journal is not None:
        journal.append(
            "HEADER",
            version=AGENT_VERSION,
            task_id=task.task_id,
            initial=sorted(task.initial),
            goal=sorted(task.goal),
            catalog=task.catalog.to_list(),
            catalog_hash=task.catalog.content_hash(),
            config=cfg.to_dict(),
            **(header_extra or {}),
        )

    s = task.initial
    plan: list[str] = []
    plan_records: list[dict] = []
    value_cache: dict = {}
    l3_calls_before = wm.l3.calls
    success = False

    while len(plan) < cfg.max_steps:
        if task.goal <= s:
            success = True
            break
        a_app = useful_actions(s, task.catalog, graph, wm)
        if not a_app and strategist is not None:
            # Alg. 2 line 6 is where the agent used to give up: nothing it
            # can do reaches the goal. That is the wall between "I was told
            # exactly how" and "work out how". The strategist proposes
            # actions the catalogue does not have -- validated against the
            # sensor vocabulary, gated by risk, and still only believed once
            # an instrument confirms the effect.
            grown = strategist(s, task.goal - s, task.catalog)
            if grown:
                task = replace(task, catalog=grown)
                wm.catalog = grown
                wm.l1.catalog = grown
                value_cache.clear()
                a_app = useful_actions(s, task.catalog, graph, wm)
        if not a_app:
            break  # Alg. 2 line 6: failure (journaled in END)

        chosen, table = gats_search(s, task.goal, task, graph, wm, cfg,
                                    value_cache, a_app)
        edge = graph.get_edge(state_key(s), chosen)
        record = {
            "step": len(plan),
            "state": state_key(s),
            "table": table,
            "chosen": chosen,
            "pred_state": edge.v_key,
            "wm": {"layer": edge.layer, "p": edge.p},
            "search_iters": cfg.budget,
            "llm_planning_calls": wm.l3.calls - l3_calls_before,
        }
        plan_records.append(record)
        if journal is not None:
            journal.append("PLAN", **record)

        s_obs = env_step(s, chosen)
        diverged = state_key(s_obs) != edge.v_key
        if journal is not None:
            journal.append(
                "OBSERVE",
                step=len(plan),
                obs_state=state_key(s_obs),
                obs_props=sorted(s_obs),
                diverged=diverged,
            )
        action = task.catalog.get(chosen)
        learn_always = action is not None and (
            not action.deterministic or not action.effects_known)
        if diverged or learn_always:
            # SS6.4: learn the observed transition (stochastic and effect-
            # unknown actions learn from every outcome, exact contracts only
            # from divergence -- an L1 divergence means the contract itself
            # is wrong), refresh
            # the edge to the world model's updated prediction, and drop
            # stale values. The refreshed edge restarts its UCB stats: its
            # prior values were backed up against a falsified successor.
            wm.record_outcome(s, chosen, s_obs)
            refreshed = wm.predict(s, chosen)
            graph.add_edge(state_key(s), chosen, refreshed.next_props,
                           refreshed.p, refreshed.layer)
            value_cache.clear()
            graph.ensure_node(s_obs)

        plan.append(chosen)
        s = s_obs

    if task.goal <= s:
        success = True

    result = EpisodeResult(
        success=success,
        plan=plan,
        final_props=s,
        steps=len(plan),
        llm_planning_calls=wm.l3.calls - l3_calls_before,
        plan_records=plan_records,
    )
    if journal is not None:
        journal.append("END", success=success, plan=plan, steps=len(plan),
                       llm_planning_calls=result.llm_planning_calls)
    return result
