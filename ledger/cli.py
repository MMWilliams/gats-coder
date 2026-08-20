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
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import AGENT_VERSION, ui, voice
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

def _backend(args, cfg: dict | None = None):
    """Resolve an LLM backend: flags win, then config "backend" block, then
    the claude CLI. Returns None for kind='none'."""
    from .llm import resolve_backend
    block = (cfg or {}).get("backend")
    block = block if isinstance(block, dict) else {}
    kind = getattr(args, "backend", None) or block.get("kind") or "claude"
    model = getattr(args, "model", None) or block.get("model")
    base_url = getattr(args, "base_url", None) or block.get("base_url")
    timeout = block.get("timeout")
    try:
        return resolve_backend(kind, model, base_url,
                               int(timeout) if timeout else None)
    except RuntimeError as exc:
        raise SystemExit(str(exc))


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
    if getattr(args, "dangerously_skip_permissions", False):
        print(ui.bad("! permissions disabled: every gate, prompt, and "
                     "policy rule is bypassed for this run"),
              file=sys.stderr)
        return attended_gate, profile
    approver = attended_gate if getattr(args, "yes", False) else tty_approver
    try:
        gate = PolicyGate(profile, approver=approver,
                          rules=_merged_rules(args, cfg))
    except ValueError as exc:
        raise SystemExit(str(exc))
    return gate, profile


def _fail(msg: str) -> int:
    print(f"{ui.bad('error:')} {msg}", file=sys.stderr)
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
    print(f"{ui.ok(ui.OK)} initialized {ui.dim(str(cfg_path))}")
    print()
    print(ui.next_steps(
        f"ledger doctor    {ui.dim('# check git, claude, and this config')}",
        'ledger run "describe the change you want"',
        f"ledger chat    {ui.dim('# just talk about the repo first')}"))
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
        gate=gate, profile=profile, max_author_attempts=author_budget,
        hooks=_hooks_of(cfg_file))
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
    if not args.json:
        print(ui.headline(winner.success,
                          f"{winner.name} wins",
                          "no attempt reached the goal"))
        print()
        for o in ranked:
            mark = ui.ok(ui.OK) if o.success else ui.bad(ui.FAIL)
            detail = o.error or (f"{o.steps} steps {ui.SEP} "
                                 f"{o.incidents} incidents")
            crown = ui.warn(f" {ui.MARK} winner") if o is winner and o.success else ""
            print(f"  {mark} {ui.bold(o.name)}  {ui.dim(detail)}{crown}")
        print()
        if winner.success:
            print(ui.kv("plan", ui.plan_chain(winner.plan)))
            print(ui.kv("branch", f"ledger/{winner.name}"))
            print(ui.kv("journal", ui.dim(str(winner.journal_path))))
        print(ui.kv("session", ui.dim(str(session.journal_path))))
        if winner.success:
            print()
            print(ui.next_steps(
                f"ledger status {winner.journal_path}",
                f"git -C {root} branch --list ledger/*"
                f"    {ui.dim('# the attempt branches')}"))
        return 0 if winner.success else 1
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
        backend = _backend(args, cfg_file)
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

    from . import tui
    from .executor import BackendUnavailable, run_repo_episode
    from .goal import GoalCompileError
    ledger_dir = root / ".ledger"
    ledger_dir.mkdir(exist_ok=True)
    # An episode is a long, silent stretch of work; say what it is doing.
    # --json stays byte-clean: the spinner is suppressed there.
    quiet = args.json or not tui.interactive()
    try:
      with tui.spinner("starting the episode") as progress:
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
            hooks=_hooks_of(cfg_file),
            progress=None if quiet else progress,
        )
    except BackendUnavailable as exc:
        return _fail(str(exc))
    except GoalCompileError as exc:
        return _fail(
            f"could not turn the task into a verifiable goal: {exc}\n"
            "  say what 'done' looks like in terms the sensors can check "
            "(a passing suite, a clean tree), or try a stronger model")
    entries = _current_run(Journal.read(str(journal_path)))
    header = entries[0] if entries and entries[0]["t"] == "HEADER" else {}
    esc = next((e for e in reversed(entries)
                if e["t"] == "ESCALATION"), None)
    if args.json:
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

    print(ui.headline(result.success, "goal reached", "goal NOT reached"))
    print()
    if header.get("goal"):
        print(ui.kv("goal", " & ".join(header["goal"])))
    print(ui.kv("plan", ui.plan_chain(result.plan)))
    stats = (f"{result.steps} steps {ui.SEP} {result.llm_planning_calls} LLM "
             f"planning calls {ui.SEP} profile {profile}")
    print(ui.kv("run", stats))
    checks = []
    checks.append(ui.ok(f"{ui.OK} 0 incidents") if executor.incidents == 0
                  else ui.bad(f"{ui.FAIL} {executor.incidents} incidents"))
    if executor.denials:
        checks.append(ui.warn(f"{executor.denials} denials"))
    print(ui.kv("verify", "  ".join(checks)))
    if esc is not None:
        print(ui.kv("escalated", ui.bad(esc.get("reason", "?"))))
    print(ui.kv("journal", ui.dim(str(journal_path))))
    print()
    if result.success:
        print(ui.next_steps(
            f"ledger status {journal_path}",
            f"ledger why {journal_path} 0    {ui.dim('# the decision table')}",
            f"ledger vis {journal_path}    {ui.dim('# HTML report')}"))
    else:
        reason = esc.get("reason") if esc else None
        hints = [f"ledger status {journal_path}"]
        if reason == "budget_exhausted":
            hints.append(
                f'ledger run "{args.task}" --name {args.name} --resume '
                f"--max-author-attempts {author_budget * 2}"
                f"    {ui.dim('# same episode, fresh budget')}")
        elif reason == "gate_denied":
            hints.append(f'ledger run "{args.task}" --name {args.name} '
                         f"--resume --yes"
                         f"    {ui.dim('# pre-approve the gated actions')}")
        else:
            hints.append(f"ledger why {journal_path} {max(result.steps - 1, 0)}"
                         f"    {ui.dim('# why the last step was chosen')}")
        print(ui.next_steps(*hints))
    return 0 if result.success else 1


def _current_run(entries: list) -> list:
    """Just this run's entries.

    A journal accumulates every run of an episode (resume appends a new
    segment, a re-run appends to the same file). Scanning the whole file
    makes a SUCCESSFUL run report a stale escalation from an earlier
    attempt, which is worse than saying nothing.
    """
    starts = [i for i, e in enumerate(entries) if e.get("t") == "HEADER"]
    return entries[starts[-1]:] if starts else entries


def _voice_session(args, cfg: dict, editor):
    """Build the speech front end, or explain exactly what is missing."""
    vcfg = cfg.get("voice") if isinstance(cfg.get("voice"), dict) else {}
    if getattr(args, "dangerously_skip_permissions", False):
        _fail("--voice cannot be combined with "
              "--dangerously-skip-permissions: a mis-heard word must never "
              "be able to approve an irreversible action")
        return None
    try:
        stt = voice.WhisperCppSTT(model=vcfg.get("whisper_model"))
    except RuntimeError as exc:
        _fail(str(exc))
        print(ui.dim("  run `ledger voice --setup` for the one-time "
                     "download instructions"), file=sys.stderr)
        return None
    try:
        recorder = voice.Recorder(device=vcfg.get("device"))
    except RuntimeError as exc:
        _fail(str(exc))
        return None
    tts = voice.resolve_tts(vcfg.get("tts", "auto"))
    return voice.VoiceSession(
        stt, tts, recorder, editor=editor,
        speak_replies=bool(vcfg.get("speak", True)))


SETUP_HELP = """One-time voice setup (everything runs locally):

  1. ffmpeg           records the microphone
       winget install ffmpeg      (macOS: brew install ffmpeg)

  2. whisper.cpp      transcribes speech, offline
       download a release build and a model, then put them at
         ~/.ledger/whisper/          (whisper-cli binary)
         ~/.ledger/whisper/models/   (ggml-base.en.bin)
       https://github.com/ggml-org/whisper.cpp/releases
       https://huggingface.co/ggerganov/whisper.cpp

  3. piper            speaks, offline
       pip install piper-tts
       put a voice (.onnx + .onnx.json) in ~/.ledger/voices/
       https://huggingface.co/rhasspy/piper-voices

Override any path with LEDGER_WHISPER_BIN, LEDGER_WHISPER_MODEL,
LEDGER_PIPER_BIN, LEDGER_PIPER_VOICE. Check with `ledger doctor`."""


def cmd_voice(args) -> int:
    """`ledger voice` == `ledger chat --voice`."""
    if args.setup:
        print(SETUP_HELP)
        return 0
    if args.devices:
        devices = voice.list_input_devices()
        if not devices:
            print("no input devices reported (platform may not support "
                  "enumeration; set voice.device in config)")
            return 1
        for d in devices:
            print(f"  {d}")
        return 0
    args.voice = True
    return cmd_chat(args)


def _episode_outcome(episode_dir: Path) -> str:
    """'succeeded' | 'failed' | 'unfinished' from the latest journal
    segment (episode.jsonl, episode.r2.jsonl, ...)."""
    def seg_key(p: Path):
        m = re.match(r"episode(?:\.r(\d+))?\.jsonl$", p.name)
        return int(m.group(1)) if m and m.group(1) else 1

    segs = sorted((p for p in episode_dir.glob("episode*.jsonl")
                   if re.match(r"episode(?:\.r\d+)?\.jsonl$", p.name)),
                  key=seg_key)
    if not segs:
        return "unfinished"
    try:
        entries = Journal.read(str(segs[-1]))
    except (OSError, ValueError):
        return "unfinished"
    end = next((e for e in reversed(entries) if e.get("t") == "END"), None)
    if end is None:
        return "unfinished"
    return "succeeded" if end.get("success") else "failed"


def _auto_episode(root: Path, task_text: str) -> tuple[str, bool]:
    """Intelligent episode tracking: name from the task, resume the episode
    if its last attempt didn't finish the job, start fresh otherwise."""
    from .session import slugify
    base = slugify(task_text)[:48] or "task"
    episodes = root / ".ledger" / "episodes"
    name, n = base, 1
    while (episodes / name).exists():
        if _episode_outcome(episodes / name) in ("failed", "unfinished"):
            return name, True
        n += 1
        name = f"{base}-{n}"
    return name, False


DEFAULT_CONFIG = {
    "test_cmd": DEFAULT_TEST_CMD,
    "profile": "attended",
    "slots": ["a", "b"],
    "max_author_attempts": DEFAULT_AUTHOR_ATTEMPTS,
}


def _chat_runner(root: Path, args, cfg_file: dict, backend):
    """The unified session's execution arm: task text in, verified episode
    out. All the tracking a run needs -- naming, resume, config -- is
    automatic; the planner and gates work exactly as in `ledger run`."""
    from .executor import run_repo_episode

    def runner(task_text: str, progress=None) -> dict:
        cfg_now = dict(cfg_file)
        cfg_path = root / ".ledger" / "config.json"
        if not cfg_path.is_file():
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_now = {**DEFAULT_CONFIG, "planner": PlanConfig().to_dict()}
            cfg_path.write_text(json.dumps(cfg_now, indent=2) + "\n",
                                encoding="utf-8")
            print(ui.dim("initialized .ledger/config.json with defaults"))
        gate, profile = _gate_and_profile(args, cfg_now)
        budget = getattr(args, "max_author_attempts", None) \
            or int(cfg_now.get("max_author_attempts",
                               DEFAULT_AUTHOR_ATTEMPTS))
        name, resume = _auto_episode(root, task_text)
        episode_dir = root / ".ledger" / "episodes" / name
        episode_dir.mkdir(parents=True, exist_ok=True)
        result, executor, journal_path = run_repo_episode(
            root, episode_dir, None, _test_cmd(args, cfg_now),
            cfg=_plan_config(args, cfg_now),
            gate=gate, profile=profile, max_author_attempts=budget,
            task_text=task_text, backend=backend,
            l2_path=str(root / ".ledger" / "l2.sqlite"),
            l3_cache_path=str(root / ".ledger" / "l3.sqlite"),
            slots=[str(s) for s in cfg_now.get("slots", ["a", "b"])],
            hooks=_hooks_of(cfg_now),
            resume=resume, progress=progress,
        )
        entries = _current_run(Journal.read(str(journal_path)))
        esc = next((e for e in reversed(entries)
                    if e["t"] == "ESCALATION"), None)
        reason = esc.get("reason") if esc else None
        plan_txt = " -> ".join(result.plan) or "(empty)"
        plain = (f"Episode {name!r} "
                 f"{'succeeded' if result.success else 'did not reach the goal'}"
                 f"{' (resumed)' if resume else ''}: {plan_txt} "
                 f"[{result.steps} steps, {executor.incidents} incidents"
                 + (f", escalated: {reason}" if reason else "") + "]")
        styled = "\n".join(filter(None, [
            ui.headline(result.success,
                        f"episode {name} {ui.DASH} goal reached",
                        f"episode {name} {ui.DASH} goal NOT reached"),
            ui.kv("plan", ui.plan_chain(result.plan)),
            ui.kv("verify",
                  (ui.ok(f"{ui.OK} 0 incidents") if executor.incidents == 0
                   else ui.bad(f"{ui.FAIL} {executor.incidents} incidents"))
                  + (f"  {ui.warn(str(executor.denials) + ' denials')}"
                     if executor.denials else "")),
            ui.kv("escalated", ui.bad(reason)) if reason else "",
            ui.kv("journal", ui.dim(str(journal_path))),
            ui.dim(f"  audit: ledger vis {journal_path}"),
        ]))
        return {"success": result.success, "plan": result.plan,
                "steps": result.steps, "incidents": executor.incidents,
                "escalation": reason, "journal": str(journal_path),
                "episode": name, "resumed": resume,
                "summary_plain": plain, "summary_styled": styled}
    return runner


def cmd_chat(args) -> int:
    root = Path(args.repo).resolve()
    cfg_file = _config(root)
    try:
        backend = _backend(args, cfg_file)
    except RuntimeError as exc:
        return _fail(str(exc))
    if backend is None:
        return _fail("chat requires an LLM backend (--backend claude)")
    journal = None
    if args.journal:
        journal = Journal(args.journal)
    conv = Conversation(backend=backend, journal=journal)
    is_repo = (root / ".git").exists()
    if is_repo:
        conv.add_repo_context(root, args.context.split(",") if args.context else None)
    runner = _chat_runner(root, args, cfg_file, backend) if is_repo else None

    # MCP: connect configured servers and expose their tools, gated.
    servers = {}
    if isinstance(cfg_file.get("mcp"), dict):
        from .mcp import load_servers
        servers = load_servers(cfg_file,
                               on_error=lambda m: print(ui.warn(m),
                                                        file=sys.stderr))
    if servers:
        tool_gate, _ = _gate_and_profile(args, cfg_file)

        def tool_call(qualified: str, arguments: dict) -> str:
            sname, _, tname = qualified.partition(":")
            srv = servers.get(sname)
            if srv is None or tname not in {t.name for t in srv.tools}:
                return f"TOOL ERROR: unknown tool {qualified!r}"
            if not tool_gate(f"mcp.{sname}:{tname}", srv.risk):
                return ("DENIED by the permission gate -- tell the user "
                        "and continue without it")
            try:
                return srv.call(tname, arguments)
            except Exception as exc:
                return f"TOOL ERROR: {exc}"

        conv.tools = {f"{sname}:{t.name}": t.description or t.name
                      for sname, srv in servers.items()
                      for t in srv.tools}
        conv.tool_call = tool_call
    try:
        if args.ask:
            print(conv.ask(args.ask))
            return 0
        from . import tui
        if not tui.interactive() and sys.stdout.isatty():
            # A terminal that could be styled but isn't: say why, once,
            # instead of silently looking broken.
            reasons = {label: detail for label, detail, ok
                       in ui.diagnostics() if not ok}
            if "colour" in reasons:
                print(ui.dim(f"styled UI off ({reasons['colour']}) {ui.DASH} "
                             "set LEDGER_COLOR=1 to force it on"))
        editor = None
        if tui.interactive():
            profile = getattr(args, "profile", None) \
                or cfg_file.get("profile", "attended")
            extra = f" {ui.SEP} {len(conv.tools)} MCP tools" if conv.tools else ""
            if getattr(args, "voice", False):
                extra += f" {ui.SEP} voice"
            print(tui.banner(root.name, profile,
                             getattr(backend, "id", "?") + extra))
            editor = tui.LineEditor(tui.COMMANDS)

        if getattr(args, "voice", False):
            vs = _voice_session(args, cfg_file, editor)
            if vs is None:
                return 1
            print(ui.dim(f"  voice: {vs.stt.id} listening {ui.SEP} {vs.tts.id} "
                         f"speaking {ui.SEP} {voice.LISTEN_HINT}"))
            return repl(conv, root, input_fn=vs.read, output_fn=vs.write,
                        runner=runner, busy=tui.spinner, greet=False)

        if editor is not None:
            return repl(conv, root, input_fn=editor.read,
                        runner=runner, busy=tui.spinner, greet=False)
        return repl(conv, root, runner=runner)
    finally:
        if journal is not None:
            journal.close()
        for srv in servers.values():
            srv.close()


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
        backend = _backend(args, _config(root))
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
    print(ui.rule("review"))
    print(reply)
    return 0


def _hooks_of(cfg: dict) -> list | None:
    return cfg.get("hooks") if isinstance(cfg.get("hooks"), list) else None


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

    root = Path(args.repo).resolve()
    cfg_file = _config(root)
    default_test = _test_cmd(args, cfg_file)
    default_slots = [str(s) for s in cfg_file.get("slots", ["a", "b"])]

    specs = []
    for item in raw:
        try:
            agent_backend = None
            if item.get("model") or item.get("backend"):
                from .llm import resolve_backend
                agent_backend = resolve_backend(
                    str(item.get("backend") or "claude"),
                    item.get("model"), item.get("base_url"))
            specs.append(AgentSpec(
                name=item["name"],
                repo_root=Path(item.get("repo", args.repo)).resolve(),
                test_cmd=item.get("test", default_test),
                task_text=item.get("task"),
                slots=item.get("slots", default_slots),
                context_files=item.get("context", []),
                backend=agent_backend,
            ))
        except (KeyError, ValueError, RuntimeError) as exc:
            return _fail(f"bad agent entry {item!r}: {exc}")

    try:
        backend = _backend(args, cfg_file)
    except RuntimeError as exc:
        return _fail(str(exc))
    # Sessions are episodes too: the same profile, policy rules and hooks
    # apply. Approving everything here regardless of config would make
    # `deny` rules mean one thing for `run` and another for `session`.
    gate, profile = _gate_and_profile(args, cfg_file)
    session = Session(
        args.out, cfg=_plan_config(args, cfg_file), backend=backend,
        gate=gate, profile=profile, hooks=_hooks_of(cfg_file),
        max_author_attempts=args.max_author_attempts
        if args.max_author_attempts is not None
        else int(cfg_file.get("max_author_attempts", DEFAULT_AUTHOR_ATTEMPTS)))
    outcomes = session.run_parallel(specs, max_workers=args.workers)

    width = max(len(o.name) for o in outcomes)
    succeeded = sum(1 for o in outcomes if o.success)
    print(ui.headline(succeeded == len(outcomes),
                      f"all {len(outcomes)} agents succeeded",
                      f"{succeeded}/{len(outcomes)} agents succeeded"))
    print()
    for o in outcomes:
        mark = ui.ok(ui.OK) if o.success else ui.bad(ui.FAIL)
        detail = o.error or f"{o.steps} steps {ui.SEP} {o.incidents} incidents"
        print(f"  {mark} {ui.bold(o.name.ljust(width))}  {ui.dim(detail)}")
    print()
    stats = session.shared_stats()
    print(ui.kv("graph", ui.dim(str(session.graph_path))))
    print(ui.kv("pooled", ui.dim(
        f"{stats.get('nodes', 0)} nodes {ui.SEP} {stats.get('edges', 0)} edges {ui.SEP} "
        f"{stats.get('dead_ends', 0)} dead ends")))
    print(ui.kv("journal", ui.dim(str(session.journal_path))))
    return 0 if all(o.success for o in outcomes) else 1


_KNOWN_CONFIG_KEYS = frozenset({
    "test_cmd", "planner", "profile", "slots", "max_author_attempts",
    "policy", "hooks", "backend", "mcp", "voice",
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
    kind = (_config(Path(args.repo).resolve()).get("backend") or {})
    kind = kind.get("kind") if isinstance(kind, dict) else None
    if kind == "anthropic":
        checks.append(("ANTHROPIC_API_KEY",
                       "set" if os.environ.get("ANTHROPIC_API_KEY")
                       else "NOT SET (backend 'anthropic' needs it)",
                       bool(os.environ.get("ANTHROPIC_API_KEY"))))
    elif kind == "openai":
        checks.append(("OPENAI_API_KEY",
                       "set" if os.environ.get("OPENAI_API_KEY")
                       else "not set (fine for keyless endpoints)", True))
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
    checks.extend(ui.diagnostics())
    checks.extend(voice.diagnostics())
    width = max(len(name) for name, _, _ in checks)
    ok = True
    for name, detail, good in checks:
        badge = ui.ok(f"{ui.OK} PASS") if good else ui.warn(f"{ui.FAIL} WARN")
        print(f"  {badge}  {name:<{width}}  {ui.dim(detail) if good else detail}")
        ok = ok and good
    if ok:
        print()
        print(ui.dim(f"all checks passed {ui.DASH} you're ready:") + " "
              + ui.accent('ledger run "describe the change you want"'))
    return 0 if ok else 1


# -- audit commands ----------------------------------------------------------

def cmd_status(args) -> int:
    all_entries = Journal.read(args.journal)
    if not all_entries:
        return _fail("empty journal")
    entries = _current_run(all_entries)
    header = entries[0] if entries[0]["t"] == "HEADER" else {}
    end = next((e for e in reversed(entries) if e["t"] == "END"), {})
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["t"]] = counts.get(e["t"], 0) + 1
    chain_ok, chain_err = Journal.verify_chain(args.journal)

    print(ui.headline(bool(end.get("success")),
                      "episode SUCCESS", "goal not reached"))
    print()
    print(ui.kv("task", str(header.get("task_text")
                            or header.get("task_id", "?"))))
    if header.get("goal"):
        print(ui.kv("goal", " & ".join(header["goal"])))
    print(ui.kv("plan", ui.plan_chain(end.get("plan", []))))
    print(ui.kv("run", f"{end.get('steps', 0)} steps {ui.SEP} "
                       f"{end.get('llm_planning_calls', 0)} LLM planning "
                       f"calls"))
    incidents = counts.get("INCIDENT", 0)
    divergences = sum(1 for e in entries
                      if e["t"] == "OBSERVE" and e.get("diverged"))
    denials = sum(1 for e in entries
                  if e["t"] == "GATE" and not e.get("approved", True))
    verify = [ui.ok(f"{ui.OK} 0 incidents") if incidents == 0
              else ui.bad(f"{ui.FAIL} {incidents} incidents"),
              ui.dim(f"{divergences} divergences")]
    if denials:
        verify.append(ui.warn(f"{denials} denials"))
    print(ui.kv("verify", "  ".join(verify)))
    esc = next((e for e in reversed(entries) if e["t"] == "ESCALATION"), None)
    if esc is not None:
        print(ui.kv("escalated",
                    ui.bad(str(esc.get("reason", "?")))
                    + ui.dim(f"  (denials={esc.get('denials', 0)}, "
                             f"author_attempts={esc.get('author_attempts', 0)}"
                             f"/{esc.get('author_attempt_limit', 0)})")))
    chain = ui.ok(f"{ui.OK} intact") if chain_ok \
        else ui.bad(f"{ui.FAIL} BROKEN ({chain_err})")
    print(ui.kv("chain", chain))
    print(ui.kv("entries", ui.dim(", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())))))
    return 0 if chain_ok else 1


def cmd_incidents(args) -> int:
    entries = Journal.read(args.journal)
    found = [e for e in entries if e["t"] == "INCIDENT"]
    if not found:
        print(f"{ui.ok(ui.OK)} no incidents recorded {ui.DASH} every claim the "
              "model made, the sensors confirmed")
        return 0
    print(ui.headline(False, "", f"{len(found)} incident(s)"))
    print()
    for e in found:
        print(f"  {ui.bad(ui.DOT)} [{e['i']}] {ui.bold(str(e.get('type', '?')))}"
              f" during {ui.accent(str(e.get('action', '?')))}")
        for prop in e.get("claimed_not_observed", []):
            print(ui.dim("        claimed but not observed: ") + prop)
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
    print(ui.kv("tasks", str(n), width=22))
    print(ui.kv("success rate",
                f"{successes}/{n} ({100.0 * successes / n:.1f}%)", width=22))
    print(ui.kv("optimal plans", f"{optimal}/{n}", width=22))
    print(ui.kv("LLM planning calls", str(llm_calls), width=22))
    print(ui.kv("plan variance (2 runs)",
                "0 (identical)" if identical else ui.bad("NONZERO"),
                width=22))
    ok = successes == n and optimal == n and llm_calls == 0 and identical
    print()
    print("M1 exit criterion:",
          ui.ok(f"{ui.OK} PASS") if ok else ui.bad(f"{ui.FAIL} FAIL"))
    return 0 if ok else 1


def cmd_verify_chain(args) -> int:
    ok, err = Journal.verify_chain(args.journal)
    if ok:
        print(f"{ui.ok(ui.OK)} chain OK {ui.DASH} every hash links, nothing "
              "has been altered")
        return 0
    print(f"{ui.bad(ui.FAIL)} chain BROKEN: {err}")
    return 1


def cmd_replay(args) -> int:
    result = run_replay(args.journal)
    if result.ok:
        print(f"{ui.ok(ui.OK)} replay OK {ui.DASH} {result.steps_compared} steps "
              "reproduced bit-exact")
        return 0
    print(f"{ui.bad(ui.FAIL)} replay MISMATCH:")
    for m in result.mismatches:
        print(f"  {ui.bad(ui.DOT)} {m}")
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
    print(f"{ui.bold(f'step {args.step}')} @ {ui.dim(plan['state'][:12])}: "
          f"chose {ui.accent(plan['chosen'])}")
    print(ui.dim(f"  predicted {ui.ARROW} {plan['pred_state'][:12]} "
                 f"(layer {plan['wm']['layer']}, p={plan['wm']['p']}) {ui.SEP} "
                 f"{plan['search_iters']} search iterations {ui.SEP} "
                 f"{plan['llm_planning_calls']} LLM planning calls"))
    print()
    rows = []
    ordered = sorted(plan["table"], key=lambda r: -r["q"])
    chosen_i = None
    for i, row in enumerate(ordered):
        if row["n"] > 0 and n_total > 0:
            ucb = row["q"] + c * math.sqrt(2.0 * math.log(n_total) / row["n"])
            ucb_s = f"{ucb:.3f}"
        else:
            ucb_s = "inf (unvisited)"
        mark = ""
        if row["a"] == plan["chosen"]:
            chosen_i = i
            mark = f"{ui.CHOSEN} chosen"
        rows.append([row["a"], str(row["n"]), f"{row['q']:.3f}",
                     ucb_s, mark])
    print(ui.table(rows, header=["arm", "N", "Q", "UCB", ""],
                   highlight=chosen_i))
    return 0


def cmd_vis(args) -> int:
    from .vis import render_file
    journal = Path(args.journal)
    if not journal.is_file():
        return _fail(f"no such journal: {journal}")
    out = Path(args.out) if args.out else journal.with_suffix(".html")
    out.write_text(render_file(journal), encoding="utf-8")
    print(f"{ui.ok(ui.OK)} wrote {ui.bold(str(out))}")
    print(ui.dim(f"  open it in a browser {ui.DASH} it is fully self-contained"))
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
    ap.add_argument("-V", "--version", action="version",
                    version=f"ledger {AGENT_VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_backend(p):
        p.add_argument("--backend", default=None,
                       choices=["claude", "anthropic", "openai", "ollama",
                                "none"],
                       help="LLM backend: claude CLI (default), Anthropic "
                            "API, OpenAI API, or any OpenAI-compatible "
                            "endpoint incl. local Ollama")
        p.add_argument("--model", default=None, help="backend model id")
        p.add_argument("--base-url", dest="base_url", default=None,
                       help="override the API base URL (OpenAI-compatible "
                            "providers: Groq, DeepSeek, Kimi, vLLM, ...)")

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
    p.add_argument("--dangerously-skip-permissions",
                   dest="dangerously_skip_permissions", action="store_true",
                   help="bypass every gate, prompt, and policy rule "
                        "(use only where a wrong action is cheap)")
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

    p = sub.add_parser("chat",
                       help="the unified session: converse AND change code")
    p.add_argument("--repo", default=".")
    p.add_argument("--ask", default=None, help="one-shot question, no REPL")
    p.add_argument("--context", default=None,
                   help="comma-separated files to inline")
    p.add_argument("--journal", default=None, help="journal the conversation")
    p.add_argument("--profile", default=None, choices=sorted(GATED_RISKS),
                   help="autonomy profile for inline episodes")
    p.add_argument("--yes", action="store_true",
                   help="pre-approve gated actions in inline episodes")
    p.add_argument("--max-author-attempts", dest="max_author_attempts",
                   type=int, default=None)
    p.add_argument("--dangerously-skip-permissions",
                   dest="dangerously_skip_permissions", action="store_true",
                   help="bypass every gate, prompt, and policy rule "
                        "(use only where a wrong action is cheap)")
    p.add_argument("--voice", action="store_true",
                   help="talk to it: local speech in and out "
                        "(confirmations stay on the keyboard)")
    add_backend(p)
    p.set_defaults(fn=cmd_chat)

    p = sub.add_parser("voice",
                       help="the unified session, hands-free (chat --voice)")
    p.add_argument("--repo", default=".")
    p.add_argument("--ask", default=None, help="one-shot question, no REPL")
    p.add_argument("--context", default=None)
    p.add_argument("--journal", default=None)
    p.add_argument("--profile", default=None, choices=sorted(GATED_RISKS))
    p.add_argument("--yes", action="store_true")
    p.add_argument("--max-author-attempts", dest="max_author_attempts",
                   type=int, default=None)
    p.add_argument("--dangerously-skip-permissions",
                   dest="dangerously_skip_permissions", action="store_true")
    p.add_argument("--setup", action="store_true",
                   help="print the one-time local setup instructions")
    p.add_argument("--devices", action="store_true",
                   help="list microphones ffmpeg can record from")
    add_backend(p)
    p.set_defaults(fn=cmd_voice, voice=True)

    p = sub.add_parser("session",
                       help="run several agents in parallel on a shared graph")
    p.add_argument("spec", help="JSON file: list of agent objects")
    p.add_argument("--repo", default=".", help="default repo for agents")
    p.add_argument("--out", default=".ledger/sessions/default")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--budget", type=int, default=None)
    p.add_argument("--max-steps", dest="max_steps", type=int, default=None)
    p.add_argument("--test", default=None, help="default test command")
    p.add_argument("--profile", default=None, choices=sorted(GATED_RISKS),
                   help="autonomy profile for every agent")
    p.add_argument("--yes", action="store_true",
                   help="pre-approve gated actions (parallel agents cannot "
                        "share one terminal prompt)")
    p.add_argument("--allow", default=None)
    p.add_argument("--deny", default=None)
    p.add_argument("--max-author-attempts", dest="max_author_attempts",
                   type=int, default=None)
    p.add_argument("--dangerously-skip-permissions",
                   dest="dangerously_skip_permissions", action="store_true",
                   help="bypass every gate, prompt, and policy rule")
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

    p = sub.add_parser("vis", help="render a journal as a self-contained "
                                   "HTML report")
    p.add_argument("journal")
    p.add_argument("-o", "--out", default=None,
                   help="output path (default: journal path with .html)")
    p.set_defaults(fn=cmd_vis)

    p = sub.add_parser("stats", help="show a persisted graph's statistics")
    p.add_argument("graph")
    p.set_defaults(fn=cmd_stats)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
