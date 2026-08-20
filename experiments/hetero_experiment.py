"""Does pooling still pay when agents do DIFFERENT work?

`pooling_experiment.py` put every agent on the identical task, so the benefit
saturates at k=2: agent 1 discovers the one correction and agents 2..N inherit
it. A step, not a curve. That is a weak claim to take to a workshop -- it shows
amortization happens without showing what governs it.

This experiment varies the task. A population draws from a family of V task
TYPES; agent k gets one of them. The question is whether a warm agent still
benefits when the agent before it may have solved something else entirely.

WHAT WE EXPECT AND WHY
----------------------
Prediction, stated before running (see RESULT at the bottom of the run output):

Propositions that distinguish these types -- `patch.queued:<id>`,
`patch.applied:<id>` -- are IN k(s). A 1-patch task and a 3-patch task
therefore never share a state key, even though both correct the same optimistic
RunSuite contract. So we expect benefit to be *per type*, not global: agent k
should pay full cost iff its type has not been seen before in this arm.

If that is right, the population-level curve is a coupon-collector shape rather
than a step: averaged over orderings, the chance that agent k draws an unseen
type falls with k, so mean divergences decay toward 0 instead of dropping at
k=2 and flattening. That is a curve, and it is governed by task diversity --
which is the honest scaling story.

If instead warm divergences stay flat at cold levels, pooling does not survive
heterogeneity at all and we report that.

DESIGN
------
  cold  agents merge into the shared store but never seed from it
  warm  agent k seeds from the graph pooled by agents 1..k-1

Task types differ in DEPTH (how many independent fixes the repo needs), so they
exercise genuinely different regions of the graph rather than cosmetic variants.
Assignment rotates by repeat so no single ordering drives the result. No LLM
anywhere: patches are pre-queued and no backend is configured, so the only
independent variable is the seed.

Run:  python -m experiments.hetero_experiment [--agents 9] [--repeats 3]
"""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from ledger.journal import Journal
from ledger.planner import PlanConfig
from ledger.session import AgentSpec, Session, slugify

# Each unit is an independent buggy module + its test. A task type of depth d
# ships the first d units broken, so depth is the number of distinct fixes.
UNITS = [
    ("a", "add", "a + b", "a - b", 5, "2, 3"),
    ("b", "sub", "a - b", "a + b", 1, "4, 3"),
    ("c", "mul", "a * b", "a + b", 6, "2, 3"),
]
TEST_CMD = [sys.executable, "-m", "pytest", "-q", "--no-header",
            "-p", "no:cacheprovider"]
GOAL = frozenset({"tests.pass:unit", "repo.clean"})


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, capture_output=True,
                   text=True, check=True)


def _buggy(u) -> str:
    key, fn, _good, bad, _exp, _args = u
    return f"def {fn}(x, y):\n    a, b = x, y\n    return {bad}\n"


def _fixed(u) -> str:
    key, fn, good, _bad, _exp, _args = u
    return f"def {fn}(x, y):\n    a, b = x, y\n    return {good}\n"


def _test(u) -> str:
    key, fn, _good, _bad, exp, args = u
    return (f"from mod_{key} import {fn}\n\n\n"
            f"def test_{fn}():\n    assert {fn}({args}) == {exp}\n")


def make_repo(root: Path, depth: int) -> Path:
    """A repo needing `depth` independent fixes, committed broken."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    for u in UNITS[:depth]:
        (root / f"mod_{u[0]}.py").write_text(_buggy(u), encoding="utf-8")
        (root / f"test_{u[0]}.py").write_text(_test(u), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", f"broken depth={depth}")
    return root


def queue_fixes(session: Session, agent: str, depth: int) -> None:
    """Pre-queue one patch per broken unit. Patch ids are stable across types
    so that any failure to share is attributable to state structure, not to
    agents inventing different names for the same fix."""
    queue = session.dir / "agents" / slugify(agent) / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    for u in UNITS[:depth]:
        diff = "".join(difflib.unified_diff(
            _buggy(u).splitlines(keepends=True),
            _fixed(u).splitlines(keepends=True),
            fromfile=f"a/mod_{u[0]}.py", tofile=f"b/mod_{u[0]}.py"))
        (queue / f"fix-{u[0]}.patch").write_text(diff, encoding="utf-8")


def journal_metrics(path: Path) -> dict:
    entries = Journal.read(str(path))
    header = entries[0] if entries and entries[0]["t"] == "HEADER" else {}
    end = next((e for e in reversed(entries) if e["t"] == "END"), {})
    seeded = header.get("seeded_graph") or {}
    return {
        "success": bool(end.get("success")),
        "steps": int(end.get("steps", 0)),
        "plan": end.get("plan", []),
        "divergences": sum(1 for e in entries
                           if e["t"] == "OBSERVE" and e.get("diverged")),
        "seeded_edges": int(seeded.get("edges", 0)),
    }


def run_arm(base: Path, repos: dict, warm: bool, order: list[int],
            budget: int) -> list[dict]:
    """Agents run SEQUENTIALLY so agent k can see 1..k-1. `order` gives the
    task depth each agent draws."""
    session = Session(base, cfg=PlanConfig(budget=budget), share_graph=warm)
    rows, seen = [], set()
    for k, depth in enumerate(order, start=1):
        name = f"agent{k:02d}"
        queue_fixes(session, name, depth)
        outcomes = session.run_parallel(
            [AgentSpec(name, repos[(depth, k)], TEST_CMD, goal=GOAL)])
        if outcomes and outcomes[0].error:
            raise RuntimeError(f"{name}: {outcomes[0].error}")
        row = journal_metrics(
            session.dir / "agents" / slugify(name) / "episode.jsonl")
        row.update(k=k, depth=depth, type_seen_before=depth in seen)
        seen.add(depth)
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, default=9)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--types", type=int, default=3, help="task depths 1..T")
    ap.add_argument("--budget", type=int, default=14)
    ap.add_argument("--out", default="experiments/results/hetero.json")
    args = ap.parse_args()

    work = Path("experiments/.work_het").resolve()
    if work.exists():
        def _force(func, path, _exc):
            import os
            import stat
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(work, onexc=_force)
    work.mkdir(parents=True, exist_ok=True)

    results: dict = {"config": vars(args), "runs": []}
    for rep in range(args.repeats):
        # Rotate assignment per repeat so no single ordering drives the result.
        order = [((rep + i) % args.types) + 1 for i in range(args.agents)]
        for arm in ("cold", "warm"):
            # A pristine repo per (arm, repeat, position): agent branches are
            # named ledger/<episode> and would otherwise collide, and a clean
            # repo guarantees no worktree from the other arm can leak in.
            repos = {(d, k): make_repo(work / f"r_{arm}{rep}_{k}", d)
                     for k, d in enumerate(order, start=1)}
            rows = run_arm(work / f"{arm}_r{rep}", repos, arm == "warm",
                           order, args.budget)
            results["runs"].append({"repeat": rep, "arm": arm, "agents": rows})
            print(f"  {arm} r{rep}: " + " ".join(
                f"k{r['k']}(d{r['depth']})={r['steps']}s/{r['divergences']}d"
                for r in rows))

    def avg(arm, k, field):
        vals = [r[field] for run in results["runs"] if run["arm"] == arm
                for r in run["agents"] if r["k"] == k]
        return sum(vals) / len(vals) if vals else 0.0

    print("\n   k | cold steps  warm steps | cold div  warm div | seeded")
    print("   --+------------------------+--------------------+-------")
    for k in range(1, args.agents + 1):
        print(f"   {k} |   {avg('cold',k,'steps'):5.2f}      {avg('warm',k,'steps'):5.2f}   "
              f"|   {avg('cold',k,'divergences'):4.2f}     {avg('warm',k,'divergences'):4.2f}   "
              f"|  {avg('warm',k,'seeded_edges'):5.1f}")

    # The prediction under test: for a warm agent, does having seen this task
    # TYPE before predict paying no divergence?
    warm_rows = [r for run in results["runs"] if run["arm"] == "warm"
                 for r in run["agents"]]
    fresh = [r for r in warm_rows if not r["type_seen_before"]]
    repeat_ = [r for r in warm_rows if r["type_seen_before"]]

    def mean(rs, f):
        return sum(r[f] for r in rs) / len(rs) if rs else 0.0

    results["summary"] = {
        f"k{k}": {f"{a}_{m}": avg(a, k, m)
                  for a in ("cold", "warm")
                  for m in ("steps", "divergences", "seeded_edges")}
        for k in range(1, args.agents + 1)
    }
    results["by_type_novelty"] = {
        "warm_first_time_type": {"n": len(fresh),
                                 "divergences": mean(fresh, "divergences"),
                                 "steps": mean(fresh, "steps")},
        "warm_repeat_type": {"n": len(repeat_),
                             "divergences": mean(repeat_, "divergences"),
                             "steps": mean(repeat_, "steps")},
    }
    results["all_succeeded"] = all(r["success"] for run in results["runs"]
                                   for r in run["agents"])
    variance = {}
    for arm in ("cold", "warm"):
        for k in range(1, args.agents + 1):
            plans = [tuple(r["plan"]) for run in results["runs"]
                     if run["arm"] == arm for r in run["agents"]
                     if r["k"] == k]
            variance[f"{arm}_k{k}"] = len(set(plans))
    results["distinct_plans_per_cell"] = variance

    b = results["by_type_novelty"]
    print("\n  RESULT (warm arm, split by whether the task TYPE was seen before)")
    print(f"    first time seeing this type : n={b['warm_first_time_type']['n']:3d}  "
          f"div={b['warm_first_time_type']['divergences']:.2f}  "
          f"steps={b['warm_first_time_type']['steps']:.2f}")
    print(f"    type already in the pool    : n={b['warm_repeat_type']['n']:3d}  "
          f"div={b['warm_repeat_type']['divergences']:.2f}  "
          f"steps={b['warm_repeat_type']['steps']:.2f}")
    print(f"\n  all agents reached goal: {results['all_succeeded']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
