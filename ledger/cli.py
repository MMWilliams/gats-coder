"""ledger CLI.

Two families of command:

  work      run, chat, session, init, doctor
  audit     status, why, incidents, replay, verify-chain, stats, demo

The audit family is the reason this tool exists: every working command leaves
a hash-chained journal that the audit family reads back.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from .chat import Conversation, repl
from .graph import TransitionGraph
from .journal import Journal
from .planner import PlanConfig, run_episode
from .replay import replay as run_replay
from .session import AgentSpec, Session
from .tasks import OPTIMAL_LENGTH, SyntheticEnv, suite
from .world_model import LayeredWorldModel

DEFAULT_TEST_CMD = ["python", "-m", "pytest", "-q"]


# -- helpers -----------------------------------------------------------------

def _backend(args):
    """Resolve an LLM backend from CLI flags. Returns None when not needed."""
    kind = getattr(args, "backend", "claude")
    if kind == "none":
        return None
    if kind == "claude":
        from .llm import ClaudeCLIBackend
        return ClaudeCLIBackend(model=getattr(args, "model", None))
    raise SystemExit(f"unknown backend {kind!r}")


def _test_cmd(args) -> list[str]:
    raw = getattr(args, "test", None)
    return raw.split() if raw else list(DEFAULT_TEST_CMD)


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


# -- work commands -----------------------------------------------------------

def cmd_init(args) -> int:
    root = Path(args.repo).resolve()
    if not (root / ".git").exists():
        return _fail(f"{root} is not a git repository")
    d = root / ".ledger"
    d.mkdir(exist_ok=True)
    cfg_path = d / "config.json"
    if cfg_path.exists() and not args.force:
        return _fail(f"{cfg_path} exists (use --force to overwrite)")
    cfg = {
        "test_cmd": _test_cmd(args),
        "planner": PlanConfig().to_dict(),
        "profile": "attended",
        "slots": ["a", "b"],
    }
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    gitignore = d / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("episodes/\nsessions/\n*.sqlite\n", encoding="utf-8")
    print(f"initialized {cfg_path}")
    print("next: ledger run \"describe the change you want\"")
    return 0


def cmd_run(args) -> int:
    root = Path(args.repo).resolve()
    if not (root / ".git").exists():
        return _fail(f"{root} is not a git repository")
    episode_dir = Path(args.out).resolve() if args.out else \
        root / ".ledger" / "episodes" / args.name
    episode_dir.mkdir(parents=True, exist_ok=True)

    slots = args.slots.split(",") if args.slots else ["a", "b"]
    try:
        backend = _backend(args)
    except RuntimeError as exc:
        return _fail(str(exc))
    if backend is None:
        return _fail("run requires an LLM backend (--backend claude)")

    from .executor import run_repo_episode
    ledger_dir = root / ".ledger"
    ledger_dir.mkdir(exist_ok=True)
    result, executor, journal_path = run_repo_episode(
        root, episode_dir, None, _test_cmd(args),
        cfg=PlanConfig(budget=args.budget, max_steps=args.max_steps),
        task_text=args.task, backend=backend,
        l2_path=str(ledger_dir / "l2.sqlite"),
        l3_cache_path=str(ledger_dir / "l3.sqlite"),
        slots=slots,
        context_files=args.context.split(",") if args.context else None,
    )
    print(f"goal:      {'reached' if result.success else 'NOT reached'}")
    print(f"plan:      {' -> '.join(result.plan) or '(empty)'}")
    print(f"steps:     {result.steps}")
    print(f"incidents: {executor.incidents}")
    print(f"LLM planning calls: {result.llm_planning_calls}")
    print(f"journal:   {journal_path}")
    print(f"audit:     ledger status {journal_path}")
    return 0 if result.success else 1


def cmd_chat(args) -> int:
    root = Path(args.repo).resolve()
    try:
        backend = _backend(args)
    except RuntimeError as exc:
        return _fail(str(exc))
    if backend is None:
        return _fail("chat requires an LLM backend (--backend claude)")
    journal = None
    if args.journal:
        journal = Journal(args.journal)
    conv = Conversation(backend=backend, journal=journal)
    if (root / ".git").exists():
        conv.add_repo_context(root, args.context.split(",") if args.context else None)
    try:
        if args.ask:
            print(conv.ask(args.ask))
            return 0
        return repl(conv, root)
    finally:
        if journal is not None:
            journal.close()


def cmd_session(args) -> int:
    spec_path = Path(args.spec)
    if not spec_path.exists():
        return _fail(f"no such spec file: {spec_path}")
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _fail(f"{spec_path}: invalid JSON ({exc})")
    if not isinstance(raw, list):
        return _fail("spec must be a JSON list of agent objects")

    specs = []
    for item in raw:
        try:
            specs.append(AgentSpec(
                name=item["name"],
                repo_root=Path(item.get("repo", args.repo)).resolve(),
                test_cmd=item.get("test", DEFAULT_TEST_CMD),
                task_text=item.get("task"),
                slots=item.get("slots", ["a", "b"]),
                context_files=item.get("context", []),
            ))
        except (KeyError, ValueError) as exc:
            return _fail(f"bad agent entry {item!r}: {exc}")

    try:
        backend = _backend(args)
    except RuntimeError as exc:
        return _fail(str(exc))
    session = Session(args.out, cfg=PlanConfig(budget=args.budget),
                      backend=backend)
    outcomes = session.run_parallel(specs, max_workers=args.workers)

    width = max(len(o.name) for o in outcomes)
    print(f"{len(outcomes)} agents, {args.workers} workers\n")
    for o in outcomes:
        mark = "ok  " if o.success else "FAIL"
        detail = o.error or f"{o.steps} steps, {o.incidents} incidents"
        print(f"  {mark} {o.name:<{width}}  {detail}")
    print(f"\nshared graph: {session.graph_path}")
    for k, v in session.shared_stats().items():
        print(f"  {k}: {v}")
    print(f"session journal: {session.journal_path}")
    return 0 if all(o.success for o in outcomes) else 1


def cmd_doctor(args) -> int:
    checks = []
    git = shutil.which("git")
    checks.append(("git", git or "NOT FOUND", git is not None))
    if git:
        ver = subprocess.run([git, "--version"], capture_output=True, text=True)
        checks.append(("git version", ver.stdout.strip(), True))
    claude = shutil.which("claude")
    checks.append(("claude CLI", claude or "NOT FOUND (needed for run/chat)",
                   claude is not None))
    checks.append(("python", sys.version.split()[0], True))
    root = Path(args.repo).resolve()
    checks.append((f"repo {root.name}", "git repo" if (root / ".git").exists()
                   else "NOT a git repository", (root / ".git").exists()))
    cfg = root / ".ledger" / "config.json"
    checks.append((".ledger/config.json", "present" if cfg.exists()
                   else "missing (run: ledger init)", cfg.exists()))
    width = max(len(name) for name, _, _ in checks)
    ok = True
    for name, detail, good in checks:
        print(f"  {'PASS' if good else 'WARN'}  {name:<{width}}  {detail}")
        ok = ok and good
    return 0 if ok else 1


# -- audit commands ----------------------------------------------------------

def cmd_status(args) -> int:
    entries = Journal.read(args.journal)
    if not entries:
        return _fail("empty journal")
    header = entries[0] if entries[0]["t"] == "HEADER" else {}
    end = next((e for e in reversed(entries) if e["t"] == "END"), {})
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["t"]] = counts.get(e["t"], 0) + 1
    chain_ok, chain_err = Journal.verify_chain(args.journal)

    print(f"task:        {header.get('task_text') or header.get('task_id', '?')}")
    if header.get("goal"):
        print(f"goal:        {' & '.join(header['goal'])}")
    print(f"outcome:     {'SUCCESS' if end.get('success') else 'not reached'}")
    print(f"plan:        {' -> '.join(end.get('plan', [])) or '(empty)'}")
    print(f"steps:       {end.get('steps', 0)}")
    print(f"LLM planning calls: {end.get('llm_planning_calls', 0)}")
    print(f"incidents:   {counts.get('INCIDENT', 0)}")
    print(f"divergences: {sum(1 for e in entries if e['t'] == 'OBSERVE' and e.get('diverged'))}")
    print(f"chain:       {'intact' if chain_ok else f'BROKEN ({chain_err})'}")
    print("entries:     " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0 if chain_ok else 1


def cmd_incidents(args) -> int:
    entries = Journal.read(args.journal)
    found = [e for e in entries if e["t"] == "INCIDENT"]
    if not found:
        print("no incidents recorded")
        return 0
    print(f"{len(found)} incident(s):\n")
    for e in found:
        print(f"  [{e['i']}] {e.get('type', '?')} during {e.get('action', '?')}")
        for prop in e.get("claimed_not_observed", []):
            print(f"        claimed but not observed: {prop}")
    return 1


def _run_suite(budget: int) -> list:
    results = []
    for task in suite():
        graph = TransitionGraph()
        wm = LayeredWorldModel(task.catalog)
        env = SyntheticEnv(task.catalog)
        results.append(
            (task, run_episode(task, graph, wm, PlanConfig(budget=budget), env.step))
        )
    return results


def cmd_demo(args) -> int:
    runs = [_run_suite(args.budget) for _ in range(2)]
    results = runs[0]
    n = len(results)
    successes = sum(1 for _, r in results if r.success)
    optimal = sum(
        1 for t, r in results
        if r.success and r.steps == OPTIMAL_LENGTH[t.task_id.split("-")[0]]
    )
    llm_calls = sum(r.llm_planning_calls for _, r in results)
    identical = all(a.plan == b.plan for (_, a), (_, b) in zip(runs[0], runs[1]))
    print(f"tasks:                {n}")
    print(f"success rate:         {successes}/{n} ({100.0 * successes / n:.1f}%)")
    print(f"optimal plans:        {optimal}/{n}")
    print(f"LLM planning calls:   {llm_calls}")
    print(f"plan variance (2 runs): {'0 (identical)' if identical else 'NONZERO'}")
    ok = successes == n and optimal == n and llm_calls == 0 and identical
    print("M1 exit criterion:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_verify_chain(args) -> int:
    ok, err = Journal.verify_chain(args.journal)
    print("chain OK" if ok else f"chain BROKEN: {err}")
    return 0 if ok else 1


def cmd_replay(args) -> int:
    result = run_replay(args.journal)
    if result.ok:
        print(f"replay OK: {result.steps_compared} steps bit-exact")
        return 0
    print("replay MISMATCH:")
    for m in result.mismatches:
        print(f"  - {m}")
    return 1


def cmd_why(args) -> int:
    entries = Journal.read(args.journal)
    header = entries[0] if entries and entries[0]["t"] == "HEADER" else {}
    c = header.get("config", {}).get("c", 1.0)
    plan = next(
        (e for e in entries if e["t"] == "PLAN" and e["step"] == args.step), None
    )
    if plan is None:
        print(f"no PLAN entry for step {args.step}")
        return 1
    n_total = sum(row["n"] for row in plan["table"])
    print(f"step {args.step} @ state {plan['state'][:12]}: chose {plan['chosen']!r}")
    print(f"  predicted -> {plan['pred_state'][:12]} "
          f"(layer {plan['wm']['layer']}, p={plan['wm']['p']})")
    print(f"  arms considered ({plan['search_iters']} search iterations, "
          f"{plan['llm_planning_calls']} LLM planning calls):")
    for row in sorted(plan["table"], key=lambda r: -r["q"]):
        if row["n"] > 0 and n_total > 0:
            ucb = row["q"] + c * math.sqrt(2.0 * math.log(n_total) / row["n"])
            ucb_s = f"{ucb:.3f}"
        else:
            ucb_s = "inf (unvisited)"
        mark = " <== chosen" if row["a"] == plan["chosen"] else ""
        print(f"    {row['a']:<28} N={row['n']:<3} Q={row['q']:.3f} "
              f"UCB={ucb_s}{mark}")
    return 0


def cmd_stats(args) -> int:
    g = TransitionGraph.load(args.graph)
    for k, v in g.stats().items():
        print(f"{k}: {v}")
    return 0


# -- parser ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ledger",
        description="Audit-first coding agent: GATS plans, the LLM writes, "
                    "sensors verify, the journal proves it.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_backend(p):
        p.add_argument("--backend", default="claude",
                       choices=["claude", "none"], help="LLM backend")
        p.add_argument("--model", default=None, help="backend model id")

    p = sub.add_parser("init", help="create .ledger/config.json in a repo")
    p.add_argument("--repo", default=".")
    p.add_argument("--test", default=None, help="test command, e.g. 'pytest -q'")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("run", help="run an agent episode against a repo")
    p.add_argument("task", help="what you want done, in plain English")
    p.add_argument("--repo", default=".")
    p.add_argument("--name", default="episode", help="episode name")
    p.add_argument("--out", default=None, help="episode directory")
    p.add_argument("--test", default=None, help="test command")
    p.add_argument("--slots", default=None,
                   help="comma-separated patch slots (default: a,b)")
    p.add_argument("--context", default=None,
                   help="comma-separated files to show the model")
    p.add_argument("--budget", type=int, default=10)
    p.add_argument("--max-steps", dest="max_steps", type=int, default=20)
    add_backend(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("chat", help="talk to the model about this repo")
    p.add_argument("--repo", default=".")
    p.add_argument("--ask", default=None, help="one-shot question, no REPL")
    p.add_argument("--context", default=None,
                   help="comma-separated files to inline")
    p.add_argument("--journal", default=None, help="journal the conversation")
    add_backend(p)
    p.set_defaults(fn=cmd_chat)

    p = sub.add_parser("session",
                       help="run several agents in parallel on a shared graph")
    p.add_argument("spec", help="JSON file: list of agent objects")
    p.add_argument("--repo", default=".", help="default repo for agents")
    p.add_argument("--out", default=".ledger/sessions/default")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--budget", type=int, default=10)
    add_backend(p)
    p.set_defaults(fn=cmd_session)

    p = sub.add_parser("doctor", help="check the local environment")
    p.add_argument("--repo", default=".")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("status", help="summarize an episode journal")
    p.add_argument("journal")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("incidents", help="list false-claim incidents")
    p.add_argument("journal")
    p.set_defaults(fn=cmd_incidents)

    p = sub.add_parser("demo", help="run the paper's 100-task synthetic suite")
    p.add_argument("--budget", type=int, default=10)
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("verify-chain", help="verify a journal's hash chain")
    p.add_argument("journal")
    p.set_defaults(fn=cmd_verify_chain)

    p = sub.add_parser("replay", help="bit-exact planner replay of a journal")
    p.add_argument("journal")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("why", help="render the decision inputs for a plan step")
    p.add_argument("journal")
    p.add_argument("step", type=int)
    p.set_defaults(fn=cmd_why)

    p = sub.add_parser("stats", help="show a persisted graph's statistics")
    p.add_argument("graph")
    p.set_defaults(fn=cmd_stats)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
