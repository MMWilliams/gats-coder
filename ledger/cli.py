"""ledger CLI.

Two families of command:

  work      run, chat, session, init, doctor
  audit     status, why, incidents, replay, verify-chain, stats, demo

The audit family is the reason this tool exists: every working command leaves
a hash-chained journal that the audit family reads back.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from . import AGENT_VERSION
from .chat import Conversation, repl
from .executor import GATED_RISKS, PolicyGate, attended_gate, tty_approver
from .graph import TransitionGraph
from .journal import Journal
from .planner import PlanConfig, run_episode
from .replay import replay as run_replay
from .session import AgentSpec, Session
from .tasks import OPTIMAL_LENGTH, SyntheticEnv, suite
from .world_model import LayeredWorldModel

DEFAULT_TEST_CMD = ["python", "-m", "pytest", "-q"]
DEFAULT_AUTHOR_ATTEMPTS = 6


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


def _config(root: Path) -> dict:
    """`.ledger/config.json`, or {} when absent/unreadable. Flags win over
    config; config wins over built-in defaults."""
    p = Path(root) / ".ledger" / "config.json"
    if not p.is_file():
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _test_cmd(args, cfg: dict | None = None) -> list[str]:
    raw = getattr(args, "test", None)
    if raw:
        return raw.split()
    from_cfg = (cfg or {}).get("test_cmd")
    if isinstance(from_cfg, list) and from_cfg:
        return [str(c) for c in from_cfg]
    return list(DEFAULT_TEST_CMD)


def _plan_config(args, cfg: dict) -> PlanConfig:
    base = PlanConfig()
    stored = cfg.get("planner")
    if isinstance(stored, dict):
        try:
            base = PlanConfig.from_dict(stored)
        except TypeError:
            pass
    overrides = {}
    if getattr(args, "budget", None) is not None:
        overrides["budget"] = args.budget
    if getattr(args, "max_steps", None) is not None:
        overrides["max_steps"] = args.max_steps
    return dataclasses.replace(base, **overrides) if overrides else base


def _merged_rules(args, cfg: dict) -> list | None:
    """Ordered policy rules, closest scope first (Kimi's scope ladder):
    --deny / --allow flags (this invocation), then config "policy" (project).
    First match wins; deny flags come before allow flags."""
    rules: list = []
    for f in (getattr(args, "deny", None) or "").split(","):
        if f.strip():
            rules.append({"action": f.strip(), "decision": "deny",
                          "reason": "--deny flag"})
    for f in (getattr(args, "allow", None) or "").split(","):
        if f.strip():
            rules.append({"action": f.strip(), "decision": "allow",
                          "reason": "--allow flag"})
    policy = cfg.get("policy")
    if isinstance(policy, dict):
        if isinstance(policy.get("rules"), list):
            rules += list(policy["rules"])
        else:
            rules += [{"action": str(f), "decision": "deny"}
                      for f in policy.get("deny", ())]
            rules += [{"action": str(f), "decision": "allow"}
                      for f in policy.get("allow", ())]
    elif isinstance(policy, list):
        rules += list(policy)
    return rules or None


def _gate_and_profile(args, cfg: dict):
    """Resolve the autonomy profile and build its risk gate (design SS7.4).

    --yes approves everything the profile would have asked about; without a
    terminal the interactive gate denies, so unattended runs must say --yes
    (or use a permissive profile) explicitly. Policy rules apply regardless
    -- a deny rule outranks --yes.
    """
    profile = getattr(args, "profile", None) or cfg.get("profile", "attended")
    if profile not in GATED_RISKS:
        raise SystemExit(f"unknown profile {profile!r} "
                         f"(choose from {sorted(GATED_RISKS)})")
    approver = attended_gate if getattr(args, "yes", False) else tty_approver
    try:
        gate = PolicyGate(profile, approver=approver,
                          rules=_merged_rules(args, cfg))
    except ValueError as exc:
        raise SystemExit(str(exc))
    return gate, profile


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
        "max_author_attempts": DEFAULT_AUTHOR_ATTEMPTS,
    }
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    gitignore = d / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("episodes/\nsessions/\n*.sqlite\n", encoding="utf-8")
    print(f"initialized {cfg_path}")
    print("next: ledger run \"describe the change you want\"")
    return 0


def _run_attempts(args, root, cfg_file, backend, gate, profile,
                  author_budget, slots) -> int:
    """Best-of-N: N identical-task agents in isolated worktrees (the session
    machinery), a deterministic winner pick, and the losers reported rather
    than hidden."""
    if not args.yes:
        return _fail("--attempts needs --yes: parallel agents cannot share "
                     "one terminal prompt")
    if args.push or args.pr or args.resume:
        return _fail("--attempts cannot combine with --push/--pr/--resume "
                     "(publish the winning branch afterwards)")
    session = Session(
        root / ".ledger" / "sessions" / f"{args.name}-attempts",
        cfg=_plan_config(args, cfg_file), backend=backend,
        gate=gate, profile=profile, max_author_attempts=author_budget)
    specs = [AgentSpec(
        name=f"attempt-{i + 1}", repo_root=root,
        test_cmd=_test_cmd(args, cfg_file), task_text=args.task,
        slots=slots,
        context_files=args.context.split(",") if args.context else [])
        for i in range(args.attempts)]
    outcomes = session.run_parallel(specs,
                                    max_workers=min(args.attempts, 4))
    ranked = sorted(outcomes,
                    key=lambda o: (not o.success, o.steps, o.name))
    winner = ranked[0]
    if args.json:
        print(json.dumps({
            "agent_version": AGENT_VERSION,
            "task": args.task,
            "attempts": [{"name": o.name, "success": o.success,
                          "steps": o.steps, "incidents": o.incidents,
                          "plan": o.plan, "error": o.error}
                         for o in outcomes],
            "winner": winner.name if winner.success else None,
            "winner_journal": str(winner.journal_path)
            if winner.success and winner.journal_path else None,
            "session_journal": str(session.journal_path),
        }))
        return 0 if winner.success else 1
    for o in ranked:
        mark = "ok  " if o.success else "FAIL"
        detail = o.error or f"{o.steps} steps, {o.incidents} incidents"
        print(f"  {mark} {o.name}  {detail}")
    if winner.success:
        print(f"\nwinner:  {winner.name} "
              f"({winner.steps} steps: {' -> '.join(winner.plan)})")
        print(f"branch:  ledger/{winner.name} (in its episode worktree)")
        print(f"journal: {winner.journal_path}")
    else:
        print("\nno attempt reached the goal")
    print(f"session journal: {session.journal_path}")
    return 0 if winner.success else 1


def cmd_run(args) -> int:
    root = Path(args.repo).resolve()
    if not (root / ".git").exists():
        return _fail(f"{root} is not a git repository")
    cfg_file = _config(root)

    if args.slots:
        slots = args.slots.split(",")
    else:
        slots = [str(s) for s in cfg_file.get("slots", ["a", "b"])]
    try:
        backend = _backend(args)
    except RuntimeError as exc:
        return _fail(str(exc))
    if backend is None:
        return _fail("run requires an LLM backend (--backend claude)")
    gate, profile = _gate_and_profile(args, cfg_file)
    author_budget = args.max_author_attempts \
        if args.max_author_attempts is not None \
        else int(cfg_file.get("max_author_attempts", DEFAULT_AUTHOR_ATTEMPTS))

    if args.attempts and args.attempts > 1:
        return _run_attempts(args, root, cfg_file, backend, gate, profile,
                             author_budget, slots)

    episode_dir = Path(args.out).resolve() if args.out else \
        root / ".ledger" / "episodes" / args.name
    episode_dir.mkdir(parents=True, exist_ok=True)

    from .executor import run_repo_episode
    ledger_dir = root / ".ledger"
    ledger_dir.mkdir(exist_ok=True)
    result, executor, journal_path = run_repo_episode(
        root, episode_dir, None, _test_cmd(args, cfg_file),
        cfg=_plan_config(args, cfg_file),
        gate=gate, profile=profile, max_author_attempts=author_budget,
        task_text=args.task, backend=backend,
        l2_path=str(ledger_dir / "l2.sqlite"),
        l3_cache_path=str(ledger_dir / "l3.sqlite"),
        slots=slots,
        context_files=args.context.split(",") if args.context else None,
        push=args.push, pr=args.pr, resume=args.resume,
        hooks=cfg_file.get("hooks")
        if isinstance(cfg_file.get("hooks"), list) else None,
    )
    if args.json:
        entries = Journal.read(str(journal_path))
        header = entries[0] if entries and entries[0]["t"] == "HEADER" else {}
        esc = next((e for e in reversed(entries)
                    if e["t"] == "ESCALATION"), None)
        print(json.dumps({
            "agent_version": AGENT_VERSION,
            "task": args.task,
            "goal": header.get("goal", []),
            "success": result.success,
            "plan": result.plan,
            "steps": result.steps,
            "profile": profile,
            "incidents": executor.incidents,
            "denials": executor.denials,
            "llm_planning_calls": result.llm_planning_calls,
            "escalation": esc.get("reason") if esc else None,
            "journal": str(journal_path),
        }))
        return 0 if result.success else 1
    print(f"goal:      {'reached' if result.success else 'NOT reached'}")
    print(f"plan:      {' -> '.join(result.plan) or '(empty)'}")
    print(f"steps:     {result.steps}")
    print(f"profile:   {profile}")
    print(f"incidents: {executor.incidents}")
    if executor.denials:
        print(f"denials:   {executor.denials} (gated actions the human refused)")
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


REVIEW_PROMPT = """You are reviewing a git diff for correctness bugs and \
risky changes.

{instructions}DIFF
{diff}

Report findings as a list, most severe first. For each: file, the problem in
one sentence, and the concrete failure it causes. If the diff looks correct,
say exactly that in one line. Do not restate the diff or pad the answer."""


def cmd_review(args) -> int:
    root = Path(args.repo).resolve()
    if not (root / ".git").exists():
        return _fail(f"{root} is not a git repository")
    try:
        backend = _backend(args)
    except RuntimeError as exc:
        return _fail(str(exc))
    if backend is None:
        return _fail("review requires an LLM backend (--backend claude)")

    if args.range:
        diff_args = ["git", "diff", args.range]
    elif args.staged:
        diff_args = ["git", "diff", "--cached"]
    else:
        diff_args = ["git", "diff", "HEAD"]
    proc = subprocess.run(diff_args, cwd=root, capture_output=True,
                          text=True, timeout=120)
    if proc.returncode != 0:
        return _fail(f"git diff failed: {proc.stderr.strip()[:300]}")
    diff = proc.stdout
    if not diff.strip():
        print("nothing to review: the diff is empty")
        return 0
    if len(diff) > 80_000:
        print(f"note: diff truncated to 80000 of {len(diff)} chars",
              file=sys.stderr)
        diff = diff[:80_000]

    from .skills_llm import project_instructions
    instructions = project_instructions(root)
    prompt = REVIEW_PROMPT.format(
        diff=diff,
        instructions=("PROJECT INSTRUCTIONS\n" + instructions + "\n\n")
        if instructions else "")
    if args.journal:
        with Journal(args.journal) as journal:
            journal.append("REVIEW_REQUEST", diff_args=diff_args,
                           diff_chars=len(diff))
            reply = backend.complete(prompt).strip()
            journal.append("REVIEW_FINDINGS", text=reply,
                           backend=getattr(backend, "id", "?"))
    else:
        reply = backend.complete(prompt).strip()
    print(reply)
    return 0


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
            agent_backend = None
            if item.get("model"):
                from .llm import ClaudeCLIBackend
                agent_backend = ClaudeCLIBackend(model=str(item["model"]))
            specs.append(AgentSpec(
                name=item["name"],
                repo_root=Path(item.get("repo", args.repo)).resolve(),
                test_cmd=item.get("test", DEFAULT_TEST_CMD),
                task_text=item.get("task"),
                slots=item.get("slots", ["a", "b"]),
                context_files=item.get("context", []),
                backend=agent_backend,
            ))
        except (KeyError, ValueError, RuntimeError) as exc:
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


_KNOWN_CONFIG_KEYS = frozenset({
    "test_cmd", "planner", "profile", "slots", "max_author_attempts",
    "policy", "hooks",
})


def _validate_config(path: Path) -> list[str]:
    """Config linter (doctor): parse errors, unknown keys, bad shapes."""
    problems: list[str] = []
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"config unreadable: {exc}"]
    if not isinstance(cfg, dict):
        return ["config must be a JSON object"]
    for key in sorted(set(cfg) - _KNOWN_CONFIG_KEYS):
        problems.append(f"unknown key {key!r} (ignored)")
    if "profile" in cfg and cfg["profile"] not in GATED_RISKS:
        problems.append(f"unknown profile {cfg['profile']!r} "
                        f"(choose from {sorted(GATED_RISKS)})")
    if "test_cmd" in cfg and not (
            isinstance(cfg["test_cmd"], list) and cfg["test_cmd"]):
        problems.append("test_cmd must be a non-empty list of strings")
    if "hooks" in cfg:
        if not isinstance(cfg["hooks"], list):
            problems.append("hooks must be a list")
        else:
            for i, h in enumerate(cfg["hooks"]):
                if not isinstance(h, dict) or not h.get("command"):
                    problems.append(f"hooks[{i}] needs a command list")
                elif h.get("event", "pre_action") not in ("pre_action",
                                                         "post_action"):
                    problems.append(f"hooks[{i}] event must be "
                                    "pre_action or post_action")
    policy = cfg.get("policy")
    if policy is not None and not isinstance(policy, (dict, list)):
        problems.append("policy must be an object or a rule list")
    return problems


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
    gh = shutil.which("gh")
    checks.append(("gh CLI", gh or "not found (only needed for --pr)", True))
    checks.append(("python", sys.version.split()[0], True))
    root = Path(args.repo).resolve()
    checks.append((f"repo {root.name}", "git repo" if (root / ".git").exists()
                   else "NOT a git repository", (root / ".git").exists()))
    cfg = root / ".ledger" / "config.json"
    checks.append((".ledger/config.json", "present" if cfg.exists()
                   else "missing (run: ledger init)", cfg.exists()))
    if cfg.exists():
        problems = _validate_config(cfg)
        if problems:
            for p in problems:
                checks.append(("config lint", p, False))
        else:
            checks.append(("config lint", "clean", True))
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
    denials = sum(1 for e in entries
                  if e["t"] == "GATE" and not e.get("approved", True))
    if denials:
        print(f"denials:     {denials}")
    esc = next((e for e in reversed(entries) if e["t"] == "ESCALATION"), None)
    if esc is not None:
        print(f"escalation:  {esc.get('reason', '?')} "
              f"(denials={esc.get('denials', 0)}, "
              f"author_attempts={esc.get('author_attempts', 0)}"
              f"/{esc.get('author_attempt_limit', 0)})")
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
                   help="comma-separated files to show the model "
                        "(default: selected automatically)")
    p.add_argument("--budget", type=int, default=None)
    p.add_argument("--max-steps", dest="max_steps", type=int, default=None)
    p.add_argument("--profile", default=None,
                   choices=sorted(GATED_RISKS),
                   help="autonomy profile: which risk classes stop for "
                        "approval (default: config, then 'attended')")
    p.add_argument("--yes", action="store_true",
                   help="pre-approve every gated action (unattended runs)")
    p.add_argument("--max-author-attempts", dest="max_author_attempts",
                   type=int, default=None,
                   help="LLM patch-authoring budget for the episode "
                        f"(default: config, then {DEFAULT_AUTHOR_ATTEMPTS})")
    p.add_argument("--push", action="store_true",
                   help="add a network-gated PushBranch action (pushes the "
                        "episode branch to origin; mention pushing in the "
                        "task so the goal compiler requires it)")
    p.add_argument("--pr", action="store_true",
                   help="add PushBranch + CreatePR actions (gh pr create; "
                        "network-gated; mention the PR in the task)")
    p.add_argument("--resume", action="store_true",
                   help="re-enter an existing episode: reuse its worktree "
                        "and branch, warm-start its graph, journal a new "
                        "segment, refresh the authoring budget")
    p.add_argument("--attempts", type=int, default=1,
                   help="best-of-N: run N isolated attempts in parallel and "
                        "report the winner (requires --yes)")
    p.add_argument("--allow", default=None,
                   help="comma-separated action families to pre-approve "
                        "for this run (policy rule, journaled)")
    p.add_argument("--deny", default=None,
                   help="comma-separated action families to block for this "
                        "run; outranks --yes and the profile")
    p.add_argument("--json", action="store_true",
                   help="print one JSON result object instead of the "
                        "human summary (journal remains the event stream)")
    add_backend(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("review",
                       help="LLM review of a git diff (working tree by default)")
    p.add_argument("--repo", default=".")
    p.add_argument("--staged", action="store_true",
                   help="review staged changes only")
    p.add_argument("--range", default=None,
                   help="git diff range, e.g. main..HEAD")
    p.add_argument("--journal", default=None,
                   help="journal the review request + findings")
    add_backend(p)
    p.set_defaults(fn=cmd_review)

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
