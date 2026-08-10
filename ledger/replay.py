"""Deterministic replay (design doc SS9.2): re-run the planner -- not the
tools -- over a journal's recorded observations and require bit-exact
agreement with the recorded PLAN entries.

A mismatch means tampering, version drift, or a planner bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .actions import Catalog
from .graph import TransitionGraph
from .journal import Journal
from .planner import PlanConfig, Task, run_episode
from .world_model import LayeredWorldModel


@dataclass
class ReplayResult:
    ok: bool
    mismatches: list[str] = field(default_factory=list)
    steps_compared: int = 0


_PLAN_FIELDS = ("state", "table", "chosen", "pred_state", "wm",
                "llm_planning_calls")


def replay(journal_path: str) -> ReplayResult:
    ok, err = Journal.verify_chain(journal_path)
    if not ok:
        return ReplayResult(ok=False, mismatches=[f"chain: {err}"])

    entries = Journal.read(journal_path)
    if not entries or entries[0]["t"] != "HEADER":
        return ReplayResult(ok=False, mismatches=["missing HEADER entry"])
    header = entries[0]

    catalog = Catalog.from_list(header["catalog"])
    if catalog.content_hash() != header["catalog_hash"]:
        return ReplayResult(ok=False, mismatches=["catalog hash mismatch"])
    task = Task(
        task_id=header["task_id"],
        initial=frozenset(header["initial"]),
        goal=frozenset(header["goal"]),
        catalog=catalog,
    )
    cfg = PlanConfig.from_dict(header["config"])

    recorded_plans = [e for e in entries if e["t"] == "PLAN"]
    recorded_obs = [e for e in entries if e["t"] == "OBSERVE"]
    recorded_end = next((e for e in entries if e["t"] == "END"), None)

    # Environment = the recorded observations, replayed in order.
    cursor = {"i": 0}

    def env_step(s, action_name):
        i = cursor["i"]
        if i >= len(recorded_obs):
            raise IndexError("replay ran past recorded observations")
        cursor["i"] = i + 1
        return frozenset(recorded_obs[i]["obs_props"])

    graph = TransitionGraph()
    wm = LayeredWorldModel(catalog)
    try:
        result = run_episode(task, graph, wm, cfg, env_step, journal=None)
    except IndexError as exc:
        return ReplayResult(ok=False, mismatches=[str(exc)])

    mismatches: list[str] = []
    if len(result.plan_records) != len(recorded_plans):
        mismatches.append(
            f"step count: replay {len(result.plan_records)} vs "
            f"recorded {len(recorded_plans)}"
        )
    for rec, live in zip(recorded_plans, result.plan_records):
        step = rec["step"]
        for f in _PLAN_FIELDS:
            if rec[f] != live[f]:
                mismatches.append(f"step {step}: field {f!r} differs")
    if recorded_end is not None and recorded_end["success"] != result.success:
        mismatches.append("END success differs")

    return ReplayResult(
        ok=not mismatches,
        mismatches=mismatches,
        steps_compared=min(len(result.plan_records), len(recorded_plans)),
    )
