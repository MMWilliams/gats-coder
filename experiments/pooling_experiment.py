"""Does agent N+1 plan better because agents 1..N ran?

The claim under test (paper SS3.3): a population of agents that pools its
VERIFIED transition structure amortizes discovery -- later agents should not
have to re-learn what earlier ones already established by observation.

DESIGN
------
Two arms, identical in every respect except one:

  cold  agents merge their graphs into the shared store but never start from
        it (Session(share_graph=False))
  warm  agent k starts from the graph pooled by agents 1..k-1

Same repo, same goal, same task, same seeds, same order, no LLM anywhere
(pre-queued patches + no backend). The ONLY independent variable is the seed.
That matters: a result contaminated by model sampling would prove nothing
about the graph.

WHAT WE EXPECT AND WHY
----------------------
L1 gives RunSuite an *optimistic* contract -- it predicts the suite passes.
On a repo with a failing test that prediction is wrong, so a cold agent
discovers the truth by DIVERGING, then re-plans. A warm agent inherits the
corrected edge and should not need to pay for that discovery again.

So the primary signal is divergences, and the secondary is steps. If both
fall with k while success holds and plan variance stays 0, that is
amortization. If they are flat, the honest report is a null result.

Run:  python -m experiments.pooling_experiment [--agents 6] [--repeats 3]
"""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from ledger.graph import TransitionGraph
from ledger.journal import Journal
from ledger.planner import PlanConfig
from ledger.session import AgentSpec, Session, slugify

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
GOAL = frozenset({"tests.pass:unit", "repo.clean"})
TEST_CMD = [sys.executable, "-m", "pytest", "-q", "--no-header",
            "-p", "no:cacheprovider"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, capture_output=True,
                   text=True, check=True)


def make_repo(root: Path) -> Path:
    """A repo with a committed bug and a failing test."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "calc.py").write_text(BUGGY, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "buggy initial commit")
    return root


def queue_fix(session: Session, agent: str) -> None:
    """Pre-queue the patch so no LLM is involved anywhere in the run."""
    queue = session.dir / "agents" / slugify(agent) / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    diff = "".join(difflib.unified_diff(
        BUGGY.splitlines(keepends=True), FIXED.splitlines(keepends=True),
        fromfile="a/calc.py", tofile="b/calc.py"))
    (queue / "fix-001.patch").write_text(diff, encoding="utf-8")


def journal_metrics(path: Path) -> dict:
    """Read what actually happened out of the hash-chained journal, not out
    of the return value -- the journal is the auditable record."""
    entries = Journal.read(str(path))
    header = entries[0] if entries and entries[0]["t"] == "HEADER" else {}
    end = next((e for e in reversed(entries) if e["t"] == "END"), {})
    divergences = sum(1 for e in entries
                      if e["t"] == "OBSERVE" and e.get("diverged"))
    seeded = header.get("seeded_graph") or {}
    return {
        "success": bool(end.get("success")),
        "steps": int(end.get("steps", 0)),
        "plan": end.get("plan", []),
        "divergences": divergences,
        "llm_planning_calls": int(end.get("llm_planning_calls", 0)),
        "seeded_edges": int(seeded.get("edges", 0)),
        "seeded_nodes": int(seeded.get("nodes", 0)),
    }


def run_arm(base: Path, repo: Path, warm: bool, n_agents: int,
            budget: int) -> list[dict]:
    """One arm: n_agents run SEQUENTIALLY so agent k can see 1..k-1."""
    session = Session(base, cfg=PlanConfig(budget=budget),
                      share_graph=warm)
    rows = []
    for k in range(1, n_agents + 1):
        name = f"agent{k:02d}"
        queue_fix(session, name)
        outcomes = session.run_parallel([AgentSpec(name, repo, TEST_CMD,
                                                   goal=GOAL)])
        if outcomes and outcomes[0].error:
            raise RuntimeError(f"{name} failed: {outcomes[0].error}")
        jpath = session.dir / "agents" / slugify(name) / "episode.jsonl"
        row = journal_metrics(jpath)
        row["k"] = k
        row["pool_after"] = session.shared_stats()
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, default=6)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--budget", type=int, default=10)
    ap.add_argument("--out", default="experiments/results/pooling.json")
    args = ap.parse_args()

    # Absolute: git worktree add resolves paths relative to the repo, not the
    # process cwd, so relative paths here produce WinError 267.
    work = Path("experiments/.work").resolve()
    if work.exists():
        # git worktrees leave read-only objects; plain rmtree fails on Windows.
        def _force(func, path, _exc):
            import os
            import stat
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(work, onexc=_force)
    work.mkdir(parents=True, exist_ok=True)

    results: dict = {"config": vars(args), "runs": []}
    for rep in range(args.repeats):
        for arm in ("cold", "warm"):
            # A pristine repo per (repeat, arm). Agent branches are named
            # ledger/<episode>, so sharing one repo across arms collides --
            # and a clean repo also guarantees no worktree left by the other
            # arm can influence this one.
            repo = make_repo(work / f"repo_{arm}_r{rep}")
            rows = run_arm(work / f"{arm}_r{rep}", repo,
                           warm=(arm == "warm"),
                           n_agents=args.agents, budget=args.budget)
            results["runs"].append({"repeat": rep, "arm": arm, "agents": rows})
            print(f"  {arm} r{rep}: "
                  + " ".join(f"k{r['k']}={r['steps']}s/{r['divergences']}d"
                             for r in rows))

    # -- determinism check: same arm, same k, across repeats -----------------
    variance = {}
    for arm in ("cold", "warm"):
        for k in range(1, args.agents + 1):
            plans = [tuple(r["plan"])
                     for run in results["runs"] if run["arm"] == arm
                     for r in run["agents"] if r["k"] == k]
            variance[f"{arm}_k{k}"] = len(set(plans))
    results["distinct_plans_per_cell"] = variance
    results["deterministic"] = all(v <= 1 for v in variance.values())

    # -- summary -------------------------------------------------------------
    def avg(arm, k, field):
        vals = [r[field] for run in results["runs"] if run["arm"] == arm
                for r in run["agents"] if r["k"] == k]
        return sum(vals) / len(vals) if vals else 0.0

    print("\n  k |  cold steps  warm steps |  cold div  warm div | seeded edges")
    print("  --+-------------------------+---------------------+-------------")
    for k in range(1, args.agents + 1):
        print(f"  {k} |    {avg('cold',k,'steps'):5.2f}      {avg('warm',k,'steps'):5.2f}  "
              f"|    {avg('cold',k,'divergences'):4.2f}     {avg('warm',k,'divergences'):4.2f}  "
              f"|   {avg('warm',k,'seeded_edges'):5.1f}")

    results["summary"] = {
        f"k{k}": {
            "cold_steps": avg("cold", k, "steps"),
            "warm_steps": avg("warm", k, "steps"),
            "cold_divergences": avg("cold", k, "divergences"),
            "warm_divergences": avg("warm", k, "divergences"),
            "warm_seeded_edges": avg("warm", k, "seeded_edges"),
        } for k in range(1, args.agents + 1)
    }
    all_success = all(r["success"] for run in results["runs"]
                      for r in run["agents"])
    results["all_succeeded"] = all_success

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n  all agents reached goal: {all_success}")
    print(f"  plan variance zero:      {results['deterministic']}")
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
