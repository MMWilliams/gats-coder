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
import time
from pathlib import Path

from . import AGENT_VERSION, receipt, signing, ui, voice
from .chat import Conversation, repl
from .executor import GATED_RISKS, PolicyGate, attended_gate, tty_approver
from .graph import TransitionGraph
from .journal import Journal, current_run as _current_run
from .planner import PlanConfig, run_episode
from .replay import replay as run_replay
from .session import AgentSpec, Session
from .taint import Provenance
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
    # "id" and "kind" both accepted: `ledger setup` writes "id", older
    # hand-written configs say "kind", and a config the resolver silently
    # ignores is worse than one it rejects.
    kind = (getattr(args, "backend", None) or block.get("id")
            or block.get("kind") or "claude")
    model = getattr(args, "model", None) or block.get("model")
    base_url = getattr(args, "base_url", None) or block.get("base_url")
    timeout = block.get("timeout")
    try:
        return resolve_backend(kind, model, base_url,
                               int(timeout) if timeout else None)
    except RuntimeError as exc:
        # Nothing configured anywhere means this is a first run, not a
        # broken one. Making someone read an error, find the setup command
        # in it, quit, and type it again is three steps where one will do.
        if not block and sys.stdin.isatty() and sys.stdout.isatty():
            print(ui.warn("LEDGER has no model backend configured yet."))
            try:
                answer = input("run setup now? [Y/n] ").strip().lower()
            except EOFError:
                answer = "n"
            if not answer or answer.startswith("y"):
                from . import wizard
                wizard.run()
                # Re-resolve against what setup just wrote, so the command
                # they originally typed carries on instead of making them
                # type it a second time.
                fresh = _config(Path(getattr(args, "repo", ".")).resolve())
                new_block = fresh.get("backend")
                new_block = new_block if isinstance(new_block, dict) else {}
                if new_block:
                    try:
                        return resolve_backend(
                            new_block.get("id") or new_block.get("kind")
                            or "claude",
                            new_block.get("model"), new_block.get("base_url"),
                            None)
                    except RuntimeError as retry_exc:
                        raise SystemExit(str(retry_exc))
        raise SystemExit(str(exc))


def _read_config(p: Path) -> dict:
    if not p.is_file():
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _config(root: Path) -> dict:
    """`~/.ledger/config.json` then `.ledger/config.json`, layered.

    Same shape as the AGENTS.md layering: the global file carries the
    preferences that hold everywhere -- a default backend, whether to launch
    what was built -- and the repo file overrides them per project. Flags win
    over both; both win over built-in defaults.

    Layered one key deep: nested blocks like "backend" merge key by key, so
    setting a global model does not have to restate the base URL in every
    repo, and a repo can override one field of it without copying the rest.
    """
    merged = _read_config(Path.home() / ".ledger" / "config.json")
    local = _read_config(Path(root) / ".ledger" / "config.json")
    for key, value in local.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


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


def _gate_and_profile(args, cfg: dict, taint_probe=None):
    """Resolve the autonomy profile and build its risk gate (design SS7.4).

    --yes approves everything the profile would have asked about; without a
    terminal the interactive gate denies, so unattended runs must say --yes
    (or use a permissive profile) explicitly. Policy rules apply regardless
    -- a deny rule outranks --yes.

    `taint_probe` is the session's provenance, so every gate a session
    builds -- the MCP tool gate and each episode's -- reads one view of
    what untrusted content is in scope. Skipping permissions skips this
    too, which is the point of the flag.
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

    # An open app window answers gates when there is no terminal to ask, which
    # is the only reason a non-terminal user can run anything at all. --yes
    # still wins: it is an explicit decision to pre-approve, and routing it to
    # a window would ask a question the user already answered.
    app_url = getattr(args, "app_url", None) or os.environ.get("LEDGER_APP_URL")
    if app_url and not getattr(args, "yes", False):
        from .app import RemoteApprover
        if RemoteApprover.reachable(app_url):
            approver = RemoteApprover(app_url)
            print(ui.dim(f"  gates will be answered in the app at {app_url}"))
        elif getattr(args, "app_url", None):
            # Asked for by name and not there. Falling through to the terminal
            # silently would be a surprise at the first gate.
            raise SystemExit(f"no ledger app is answering at {app_url}")
    try:
        gate = PolicyGate(profile, approver=approver,
                          rules=_merged_rules(args, cfg),
                          taint_probe=taint_probe)
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
    from .vote import rank
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()
    ranked = rank(outcomes, base) if base else sorted(
        outcomes, key=lambda o: (not o.success, o.steps, o.name))
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


def _product_options(args, cfg: dict) -> dict:
    """Resolve the "product" config block and its flags.

        "product": {
          "kind": "game",              auto | none | game | web | cli |
                                       service | library
          "review": true,              the pre-flight "is this usable" pass
          "require": ["controls", ...] only these dimensions
        }

    Auto-detection reads the task, so the common case needs no config at
    all: "build a three.js racing game" is a game without anyone saying so.
    """
    from .product import detect_kind

    block = cfg.get("product")
    block = block if isinstance(block, dict) else {}
    kind = getattr(args, "product_kind", None) or block.get("kind") or "auto"
    if kind == "auto":
        kind = detect_kind(getattr(args, "task", "") or "")
    elif kind == "none":
        kind = ""
    review = getattr(args, "product_review", True)
    if review and "review" in block:
        review = bool(block["review"])
    require = block.get("require")
    return {"product_kind": kind or "",
            "product_require": require if isinstance(require, list) else None,
            "product_review": bool(review)}


def _all_attempts_timed_out(journal_path) -> bool:
    """Did every authoring attempt in this run die on the clock?

    Only true when there was at least one attempt and none of them produced
    anything for a reason other than a timeout -- so a run that failed for
    real reasons is never mislabelled as a deadline problem.
    """
    try:
        entries = Journal.read(str(journal_path))
    except Exception:
        return False
    claims = []
    for e in entries:
        if e.get("t") == "EXEC" and str(e.get("action", "")).startswith(
                "AuthorPatch") and not e.get("ok"):
            claims.extend(str(c) for c in e.get("claims", []))
    return bool(claims) and all("timed out" in c for c in claims)


def _launch_if_asked(args, cfg, repo_root):
    """Start what was just built, when the run asked for it and succeeded.

    Config `"launch"` may be true (detect), a command string, or absent.
    `--launch` turns it on for one run, `--no-launch` off, and the flag wins
    over config so a scripted run can always opt out.
    """
    want = getattr(args, "launch", None)
    if want is False:                       # --no-launch
        return None
    setting = cfg.get("launch") if isinstance(cfg, dict) else None
    if want is None and not setting:
        return None
    command = None
    if isinstance(want, str):
        command = want
    elif isinstance(setting, str):
        command = setting

    from .launch import launch
    started = launch(repo_root, command=command)
    if started is None:
        print(ui.dim("  nothing obvious to launch here (no index.html and no "
                     "npm start script) -- pass --launch \"<command>\""),
              file=sys.stderr)
    return started


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
            author_timeout=next(
                (t for t in (getattr(args, "author_timeout", None),
                             (cfg_file.get("backend") or {}).get("author_timeout"))
                 if t is not None), None),
            strategise=(getattr(args, "strategise", False)
                        or bool(cfg_file.get("strategise"))),
            research=getattr(args, "research", True),
            **_product_options(args, cfg_file),
            design_review=(getattr(args, "design_review", False)
                           or bool((cfg_file.get("design") or {}).get("review"))),
            design_threshold=(getattr(args, "design_threshold", None)
                              or (cfg_file.get("design") or {}).get("threshold")
                              or 6.5),
            design_model=(getattr(args, "design_model", None)
                          or (cfg_file.get("design") or {}).get("model") or ""),
            task_text=args.task, backend=backend,
            l2_path=str(ledger_dir / "l2.sqlite"),
            l3_cache_path=str(ledger_dir / "l3.sqlite"),
            slots=slots,
            context_files=args.context.split(",") if args.context else None,
            push=args.push, pr=args.pr, resume=args.resume,
            hooks=_hooks_of(cfg_file),
            # Contracted MCP tools become planner actions; an absent
            # `mcp` block simply yields none.
            mcp_config=cfg_file,
            progress=None if quiet else progress,
            required_patch_slot=getattr(args, "require_patch_slot", None),
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
        # The episode's WORKTREE, not the repo root. Everything the run just
        # built lives in the worktree until somebody merges it, so launching
        # the root started the version without the work in it -- or, as
        # happened, found no index.html at all and said there was nothing to
        # launch immediately after building a game.
        built_in = episode_dir / "worktree"
        started = _launch_if_asked(
            args, cfg_file, built_in if built_in.is_dir() else repo_root)
        print(ui.next_steps(
            f"ledger status {journal_path}",
            f"ledger why {journal_path} 0    {ui.dim('# the decision table')}",
            f"ledger vis {journal_path}    {ui.dim('# HTML report')}"))
        if started is not None:
            print()
            print(ui.kv("launched", started.note))
            if started.url:
                print(ui.kv("open", started.url))
    else:
        reason = esc.get("reason") if esc else None
        hints = [f"ledger status {journal_path}"]
        # "budget exhausted" after every attempt died on the clock is a
        # deadline problem wearing a budget problem's clothes: doubling the
        # attempts just buys more timeouts. Say what actually needs changing.
        if _all_attempts_timed_out(journal_path):
            print(ui.warn(
                "  every authoring attempt hit the backend timeout, so no "
                "code was written at all."))
            print(ui.dim(
                "  raise it: --author-timeout 3600, or set "
                '"backend": {"author_timeout": 3600} in .ledger/config.json'))
            print()
        if reason == "infeasible":
            print(ui.warn("  the author argued the task cannot be done as specified:"))
            print(ui.dim("  " + (esc.get("verdict") or "")[:600]))
            print()
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


def _chat_runner(root: Path, args, cfg_file: dict, backend, provenance=None):
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
        gate, profile = _gate_and_profile(args, cfg_now, provenance)
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
            mcp_config=cfg_now,
            resume=resume, progress=progress,
        )
        entries = _current_run(Journal.read(str(journal_path)))
        esc = next((e for e in reversed(entries)
                    if e["t"] == "ESCALATION"), None)
        reason = esc.get("reason") if esc else None
        plan_txt = " -> ".join(result.plan) or "(empty)"
        branch = f"ledger/{name}"
        worktree = episode_dir / "worktree"
        plain = (f"Episode {name!r} "
                 f"{'succeeded' if result.success else 'did not reach the goal'}"
                 f"{' (resumed)' if resume else ''}: {plan_txt} "
                 f"[{result.steps} steps, {executor.incidents} incidents"
                 + (f", escalated: {reason}" if reason else "") + "]. "
                 f"Its work is on branch {branch} in {worktree}.")
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
        from .repo import fast_forward
        return {"success": result.success, "plan": result.plan,
                "steps": result.steps, "incidents": executor.incidents,
                "escalation": reason, "journal": str(journal_path),
                "episode": name, "resumed": resume,
                "branch": branch, "worktree": str(worktree),
                # The session offers to bring a verified result into the
                # repository the conversation is looking at; until then the
                # model would truthfully report a repo with nothing in it.
                "merge": ((lambda: fast_forward(root, branch))
                          if result.success else None),
                "merge_auto": bool(getattr(args, "yes", False)),
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
    # One view of what untrusted content is in scope, shared by the tool
    # gate, the conversation, and every episode the session launches. A
    # tool result read at 10:01 has to still constrain the episode started
    # at 10:04, which is the whole reason it is per-session and not
    # per-call.
    provenance = Provenance()
    conv = Conversation(backend=backend, journal=journal,
                        provenance=provenance)
    chat_cfg = cfg_file.get("chat")
    if isinstance(chat_cfg, dict) and chat_cfg.get("context_chars"):
        conv.budget_chars = int(chat_cfg["context_chars"])
    is_repo = (root / ".git").exists()
    if is_repo:
        conv.add_repo_context(root, args.context.split(",") if args.context else None)
    runner = _chat_runner(root, args, cfg_file, backend, provenance) \
        if is_repo else None

    # MCP: connect configured servers and expose their tools, gated.
    servers = {}
    if isinstance(cfg_file.get("mcp"), dict):
        from .mcp import load_servers
        servers = load_servers(cfg_file,
                               on_error=lambda m: print(ui.warn(m),
                                                        file=sys.stderr))
    if servers:
        tool_gate, _ = _gate_and_profile(args, cfg_file, provenance)

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


def cmd_setup(args) -> int:
    from . import credentials, wizard

    if args.list_keys:
        held = credentials.stored()
        if not held:
            print(ui.dim("no stored keys "
                         f"({credentials.path()} is absent or empty)"))
        for provider, masked in sorted(held.items()):
            env = credentials.ENV_VAR.get(provider, "")
            live = os.environ.get(env) if env else None
            note = ui.dim(f"  ({env} is set and wins)") if live else ""
            print(f"  {provider:<12} {masked}{note}")
        return 0

    if args.forget:
        gone = credentials.forget(args.forget)
        print(f"forgot {args.forget}" if gone
              else f"no stored key for {args.forget}")
        return 0 if gone else 1

    if args.key is not None:
        import getpass
        provider = args.key if isinstance(args.key, str) else "anthropic"
        # Read it rather than take it as an argument: a key on the command
        # line lands in shell history and in the process list, where it
        # outlives the terminal.
        key = getpass.getpass(f"paste your {provider} key (hidden): ").strip()
        if not key:
            print("nothing entered", file=sys.stderr)
            return 1
        path = credentials.put(provider, key)
        print(f"{ui.OK} saved to {path} {ui.dim('(owner-readable only)')}")
        return 0

    return wizard.run(want_voice=args.voice)


def cmd_doctor(args) -> int:
    checks = []
    git = shutil.which("git")
    checks.append(("git", git or "NOT FOUND", git is not None))
    if git:
        ver = subprocess.run([git, "--version"], capture_output=True, text=True)
        checks.append(("git version", ver.stdout.strip(), True))
    backend_cfg = _config(Path(args.repo).resolve()).get("backend") or {}
    backend_cfg = backend_cfg if isinstance(backend_cfg, dict) else {}
    kind = backend_cfg.get("id") or backend_cfg.get("kind") or "claude"
    if kind == "claude":
        claude = shutil.which("claude")
        checks.append(("claude CLI", claude or "NOT FOUND (selected backend)",
                       claude is not None))
    elif kind == "ollama":
        ollama = shutil.which("ollama")
        model = backend_cfg.get("model")
        checks.append(("Ollama", ollama or "NOT FOUND (selected backend)",
                       ollama is not None))
        if ollama and model:
            listing = subprocess.run([ollama, "list"], capture_output=True,
                                     text=True, timeout=30)
            present = any(line.split() and line.split()[0] == model
                          for line in listing.stdout.splitlines()[1:])
            checks.append(("local model", model if present else
                           f"{model} NOT INSTALLED", present))
    gh = shutil.which("gh")
    checks.append(("gh CLI", gh or "not found (only needed for --pr)", True))
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


def _receipt_path(journal: str, scope: str) -> Path:
    """Episode receipts sit beside the segments they cover; a single-run
    receipt is named after its own file, so the two never collide."""
    path = Path(journal)
    if path.is_dir():
        return path / "receipt.json"
    if scope == "episode":
        return path.parent / "receipt.json"
    return path.with_suffix(".receipt.json")


def cmd_receipt(args) -> int:
    scope = "run" if args.run else "episode"
    try:
        key = signing.load_key(args.key)
        doc = receipt.issue(args.journal, key, scope=scope)
    except (OSError, ValueError) as exc:
        return _fail(str(exc))

    if args.stdout:
        print(json.dumps(doc, indent=2, sort_keys=True))
        return 0

    out = Path(args.out) if args.out else _receipt_path(args.journal, scope)
    receipt.write(doc, out)

    totals, prov = doc["totals"], doc["provenance"]
    segments = doc["journal"]["segments"]
    print(ui.headline(doc["outcome"]["success"], "episode SUCCESS",
                      "goal not reached"))
    print()
    print(ui.kv("task", str(doc["episode"]["task"])))
    if totals["runs"] > 1 or len(segments) > 1:
        # The whole point of attesting an episode rather than a file: the
        # attempt that failed and the attempt that worked are both in here.
        print(ui.kv("attempts", f"{totals['runs']} runs across "
                                f"{len(segments)} segment(s)"))
        for number, run in enumerate(doc["runs"]):
            verdict = ui.ok("reached the goal") if run["success"] \
                else ui.bad(run["escalation"] or "did not finish")
            # A run's plan holds what the planner attempted, refusals
            # included, so the denial count is what makes it readable.
            denied = sum(1 for d in doc["decisions"]
                         if d["run"] == number and not d["approved"])
            print(ui.dim(f"    run {number}  {run['file']}  "
                         f"{run['profile']}  ") + verdict
                  + ui.dim(f"  {run['steps']} steps"
                           + (f", {denied} denied" if denied else "")))
    print(ui.kv("policy", ", ".join(sorted(doc["policy"]["gated_risks"]))
                + f" {ui.SEP} {len(doc['decisions'])} gated decisions, "
                  f"{totals['denials']} denied"))
    sources = prov["external_sources"]
    if not sources:
        print(ui.kv("untrusted", ui.ok(f"{ui.OK} nothing external entered "
                                       "this episode")))
    else:
        print(ui.kv("untrusted", ui.warn(f"{len(sources)} source(s)")
                    + ui.dim("  " + ", ".join(sources[:3]))))
        exposed = prov["privileged_after_external"]
        print(ui.kv("exposure", ui.bad(
            f"{len(exposed)} privileged action(s) ran after") if exposed
            else ui.ok(f"{ui.OK} no privileged action ran after")))
    if prov["unexplained_sources"]:
        print(ui.kv("unexplained", ui.warn(
            f"{len(prov['unexplained_sources'])} source(s) the gate acted "
            "on that no journaled ingress explains")))
    for gap in prov["gaps"]:
        print(ui.kv("gap", ui.bad(gap)))
    print(ui.kv("chain", ui.ok(f"{ui.OK} intact")
                if doc["journal"]["chain_ok"]
                else ui.bad(f"{ui.FAIL} BROKEN")))
    print(ui.kv("issuer", doc["sig"]["key_id"]
                + ui.dim(f"  ({signing.key_path(args.key)})")))
    print()
    print(f"{ui.ok(ui.OK)} receipt written to {out} {ui.DASH} anyone can "
          f"check it with: ledger verify-receipt {out.name} --key-id "
          f"{doc['sig']['key_id']}")
    return 0


def cmd_verify_receipt(args) -> int:
    try:
        doc = receipt.read(args.receipt)
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(f"cannot read {args.receipt}: {exc}")

    verdict = receipt.verify(doc, args.journal)
    ok, problems = verdict.ok, list(verdict.problems)
    sig = doc.get("sig") or {}
    if args.key_id and sig.get("key_id") != args.key_id:
        # A signature that verifies proves somebody stood behind the claims,
        # not that it was the somebody you meant.
        ok = False
        problems.append(f"issued by key {sig.get('key_id')}, expected "
                        f"{args.key_id}")

    totals = doc.get("totals") or {}
    print(ui.headline(ok, "receipt VERIFIED", "receipt NOT verified"))
    print()
    print(ui.kv("issuer", str(sig.get("key_id"))))
    print(ui.kv("checked", "signature + journal" if args.journal
                else ui.warn("signature only ")
                + ui.dim("(pass --journal to check the claims against the "
                         "chain)")))
    print(ui.kv("task", str((doc.get("episode") or {}).get("task"))))
    print(ui.kv("attests", f"{totals.get('runs', 0)} run(s) {ui.SEP} "
                           f"{totals.get('steps', 0)} steps {ui.SEP} "
                           f"{len(doc.get('decisions') or [])} gated "
                           f"decisions {ui.SEP} "
                           f"{totals.get('incidents', 0)} incidents"))
    for problem in problems:
        print(f"  {ui.bad(ui.DOT)} {problem}")
    for note in verdict.notes:
        print(f"  {ui.warn(ui.DOT)} {note}")
    return 0 if ok else 1


def cmd_app(args) -> int:
    """The desktop control surface. The Tauri shell points a window here."""
    from . import app as app_mod

    root = Path(args.repo).resolve()
    try:
        server, approver, url = app_mod.serve(root, host=args.host,
                                              port=args.port)
    except OSError as exc:
        return _fail(f"cannot bind {args.host}:{args.port} ({exc})")

    print(ui.kv("serving", url))
    print(ui.kv("repo", str(root)))
    print(ui.kv("gates", "answered in the window; without it they deny after "
                         f"{app_mod.GATE_TIMEOUT_S}s"))
    if not args.no_open:
        import webbrowser
        webbrowser.open(url)
    print()
    print(ui.dim("  Ctrl+C to stop"))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        server.shutdown()
        server.server_close()


def cmd_key(args) -> int:
    key = signing.load_key(args.name)
    published = {"alg": key["alg"], "key_id": key["key_id"],
                 "public": key["public"]}
    if args.export:
        Path(args.export).write_text(
            json.dumps(published, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"{ui.ok(ui.OK)} public key written to {args.export}")
        return 0
    print(ui.kv("key id", key["key_id"]))
    print(ui.kv("public", key["public"]))
    print(ui.kv("file", ui.dim(str(signing.key_path(args.name)))))
    print()
    print(ui.dim("  the signing seed never leaves that file. publish the two "
                 "lines above and a counterparty can pin --key-id, which is "
                 "what makes a valid signature by a stranger fail."))
    return 0


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


def cmd_rewind(args) -> int:
    """Put an episode's worktree back to the tree recorded before a step."""
    from .receipt import segment_paths
    from .snapshots import Snapshots
    episode = Path(args.episode)
    snaps = episode / "snapshots.git"
    if not snaps.is_dir():
        return _fail(f"{episode} has no snapshots (recorded from v0.15 on)")
    trees = []
    for seg in segment_paths(episode):
        for line in seg.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("t") == "EXEC" and entry.get("tree"):
                trees.append((entry["action"], entry["tree"]))
    if not trees:
        return _fail("no snapshot was recorded in this episode's journal")
    if args.step is None:
        for i, (action, tree) in enumerate(trees, 1):
            print(f"  {i:>3}  {tree[:12]}  before {action}")
        return 0
    if not 1 <= args.step <= len(trees):
        return _fail(f"step must be 1..{len(trees)}")
    action, tree = trees[args.step - 1]
    backup = Snapshots(snaps, episode / "worktree").restore(tree)
    print(f"{ui.ok(ui.OK)} worktree restored to the tree before {action} "
          f"({tree[:12]})")
    print(ui.dim(f"  the tree it replaced is {backup} (snapshotted first)"))
    return 0


def cmd_stats(args) -> int:
    g = TransitionGraph.load(args.graph)
    for k, v in g.stats().items():
        print(f"{k}: {v}")
    return 0


def cmd_plan_mode(args) -> int:
    """A design document only; no episode or candidate patch is started."""
    from .modes import create_plan
    root = Path(args.repo).resolve()
    backend = _backend(args, _config(root))
    if backend is None:
        return _fail("plan mode requires a model backend")
    dest = Path(args.out).resolve() if args.out else (
        root / ".ledger" / "plans" / f"plan-{int(time.time())}.md")
    try:
        create_plan(args.task, backend, dest)
    except (ValueError, FileExistsError, OSError, RuntimeError) as exc:
        return _fail(str(exc))
    print(dest)
    return 0


def cmd_browse(args) -> int:
    """Read a public web page as bounded text for documentation work."""
    from .web import read_page
    try:
        print(read_page(args.url, args.max_chars))
    except (OSError, ValueError, UnicodeError) as exc:
        return _fail(str(exc))
    return 0


def cmd_brief(args) -> int:
    """Interrogate a request and print what it decides the build must be.

    The same step every episode now runs before its first patch, exposed on
    its own so the answers can be read -- and argued with -- before any
    budget is spent on them.
    """
    from .brief import write_brief
    root = Path(args.repo).resolve()
    cfg = _config(root)
    backend = _backend(args, cfg)
    if backend is None:
        return _fail("brief requires a model backend")
    kind = _resolved_kind(getattr(args, "product_kind", None) or "auto",
                          args.task)
    destination = Path(args.out).resolve() if args.out else (
        root / ".ledger" / "briefs" / f"brief-{int(time.time())}.json")
    brief = write_brief(args.task, kind, backend, destination)
    if brief is None:
        return _fail("the model did not produce any checkable requirements")
    print(ui.bold(f"questions it asked itself ({kind or 'unclassified'})"))
    for number, question in enumerate(brief.questions, 1):
        print(f"  {number}. {ui.console_safe(question)}")
    print()
    print(ui.bold(f"what it decided the build must be  ({brief.digest()})"))
    for requirement in brief.requirements:
        print(f"  {ui.bold(requirement.id)}  {ui.console_safe(requirement.text)}")
        if requirement.check:
            print(ui.dim("    how to tell: "
                         + ui.console_safe(requirement.check)))
    print()
    print(ui.dim(str(destination)))
    return 0


def cmd_research_mode(args) -> int:
    """Generate a domain-specific provisional result and journal its evidence."""
    from .domain_workers import propose
    from .lab_contracts import EvidenceStore, ProblemSpec
    root = Path(args.repo).resolve()
    backend = _backend(args, _config(root))
    if backend is None:
        return _fail("research mode requires a model backend")
    problem = ProblemSpec(
        problem_id=args.id or f"research-{int(time.time())}",
        domain=args.domain, input_artifacts=tuple(args.input or ()),
        assumptions=tuple(args.assumption or ()),
        constraints=tuple(args.constraint or ()),
        required_output=args.task, task_budget=args.budget,
        evaluation_contract=args.contract)
    store = EvidenceStore(root / ".ledger" / "research" / "evidence.jsonl")
    try:
        store.append("problem", problem)
        candidate = propose(problem, backend, store)
    except (OSError, ValueError, RuntimeError) as exc:
        return _fail(str(exc))
    print(json.dumps(dataclasses.asdict(candidate), indent=2,
                     ensure_ascii=True))
    print("Evidence: " + str(store.path))
    return 0


def _resolved_kind(kind: str, task: str) -> str:
    from .product import detect_kind
    if kind == "auto":
        return detect_kind(task or "")
    return "" if kind == "none" else kind


def cmd_iterative_mode(args) -> int:
    """Repeat verified episodes in one worktree, preserving every attempt."""
    from .executor import reviewed_already
    from .modes import Attempt, command_acceptor, run_until
    from .product import PROBED_KINDS
    root = Path(args.repo).resolve()
    name = args.name or f"{args.cmd}-{int(time.time())}"
    journal = root / ".ledger" / "episodes" / name / "episode.jsonl"
    worktree = journal.parent / "worktree"
    prior_segments = list(journal.parent.glob("episode*.jsonl"))
    iteration_offset = len(prior_segments)
    verifier = (command_acceptor(args.accept, root)
                if args.cmd == "goal" else None)

    def episode(instruction: str, number: int) -> Attempt:
        slot = f"iteration{iteration_offset + number}"
        previous_journal = None
        if number == 1 and prior_segments:
            previous_journal = max(prior_segments,
                                   key=lambda p: p.stat().st_mtime_ns)
            instruction += ("\n\nResume from the previous episode: "
                            + str(previous_journal))
        elif number > 1:
            completed_segments = list(journal.parent.glob("episode*.jsonl"))
            if completed_segments:
                previous_journal = max(
                    completed_segments, key=lambda p: p.stat().st_mtime_ns)
        if previous_journal is not None:
            evidence = _recent_failure_context(previous_journal)
            if evidence:
                instruction += ("\n\nMost recent failure evidence (fix the "
                                "specific cause before broad rewrites):\n"
                                + evidence)
        # The product is inferred, never assumed: a loop that told every
        # build it was "a library" handed a chess game the library
        # checklist and shipped it with a test API and no way to click a
        # piece. Kind comes from the task unless stated; the usability
        # review runs once per episode (its findings are journaled, and
        # later segments read them back); game/web builds are probed and
        # photographed on every change.
        kind = getattr(args, "product_kind", None) or "auto"
        run_args = ["run", instruction, "--repo", str(root), "--name", name,
                    "--kind", kind, "--no-launch",
                    "--slots", slot, "--require-patch-slot", slot]
        if number > 1 or prior_segments:
            run_args.append("--resume")
        if reviewed_already(journal.parent):
            run_args.append("--no-product-review")
        if getattr(args, "design_review", True) and _resolved_kind(
                kind, args.task) in PROBED_KINDS:
            run_args.append("--design-review")
        if args.backend:
            run_args += ["--backend", args.backend]
        if args.model:
            run_args += ["--model", args.model]
        if args.base_url:
            run_args += ["--base-url", args.base_url]
        if args.test:
            run_args += ["--test", args.test]
        if args.context:
            run_args += ["--context", args.context]
        if args.yes:
            run_args.append("--yes")
        if args.no_research:
            run_args.append("--no-research")
        if args.author_timeout:
            run_args += ["--author-timeout", str(args.author_timeout)]
        parsed = build_parser().parse_args(run_args)
        code = parsed.fn(parsed)
        segments = list(journal.parent.glob("episode*.jsonl"))
        latest = max(segments, key=lambda p: p.stat().st_mtime_ns) if segments else journal
        return Attempt(code == 0, worktree, latest,
                       "episode succeeded" if code == 0 else "episode failed")

    try:
        attempts = run_until(args.task, episode, mode=args.cmd,
                             accept=verifier, max_iterations=args.max_iterations,
                             pause_seconds=args.pause,
                             backend_down=_iteration_backend_down)
    except KeyboardInterrupt:
        print("\nstopped; episode artifacts preserved at " + str(journal.parent))
        return 130
    except Exception:
        from .self_heal import (current_traceback, resource_exhaustion,
                                try_repair)

        details = current_traceback()
        if not args.self_heal:
            print(details, file=sys.stderr)
            return 1
        if resource_exhaustion(details):
            retry = int(os.environ.get("LEDGER_RESOURCE_RETRY", "0"))
            if retry < 3:
                delay = 20 * (retry + 1)
                print(f"Host memory exhausted; retrying Ledger in {delay}s "
                      f"({retry + 1}/3).", file=sys.stderr)
                time.sleep(delay)
                environment = os.environ.copy()
                environment["LEDGER_RESOURCE_RETRY"] = str(retry + 1)
                try:
                    # No cwd override: sys.argv[1:] may carry a --repo given
                    # relative to the directory Ledger was invoked from, and
                    # this resumes that same invocation. Pinning cwd to the
                    # Ledger package's own source root silently redirected a
                    # relative --repo into gats-coder itself and spent an
                    # entire retry budget "fixing" the wrong project.
                    return subprocess.run([sys.executable, "-m", "ledger",
                                           *sys.argv[1:]],
                                          env=environment).returncode
                except OSError as exc:
                    print(str(exc), file=sys.stderr)
                    return 1
            print(details, file=sys.stderr)
            return 1
        config_backend = _config(root).get("backend") or {}
        if not isinstance(config_backend, dict):
            config_backend = {}
        backend_options = []
        backend_id = args.backend or config_backend.get("id")
        model = args.model or config_backend.get("model")
        base_url = args.base_url or config_backend.get("base_url")
        if backend_id:
            backend_options.extend(["--backend", str(backend_id)])
        if model:
            backend_options.extend(["--model", str(model)])
        if base_url:
            backend_options.extend(["--base-url", str(base_url)])
        depth = int(os.environ.get("LEDGER_SELF_HEAL_DEPTH", "0"))
        print("Ledger framework failure; attempting isolated repair.",
              file=sys.stderr)
        if try_repair(details, source=Path(__file__).resolve().parents[1],
                      episode_dir=journal.parent, depth=depth,
                      backend_options=backend_options):
            environment = os.environ.copy()
            environment["LEDGER_SELF_HEAL_DEPTH"] = str(depth + 1)
            print("Ledger repair passed tests; resuming episode.",
                  file=sys.stderr)
            # Same reasoning as the resource-retry respawn above: resume from
            # the invocation directory, not the Ledger source root, so a
            # relative --repo still points at the user's project.
            return subprocess.run([sys.executable, "-m", "ledger",
                                   *sys.argv[1:]],
                                  env=environment).returncode
        print(details, file=sys.stderr)
        return 1
    if args.cmd == "goal":
        achieved = bool(attempts and attempts[-1].success and
                        verifier is not None and _last_accepted(journal))
        return 0 if achieved else 1
    return 0 if any(attempt.success for attempt in attempts) else 1


def _iteration_backend_down(attempt: "Attempt") -> bool:
    """True when every AuthorPatch call in this iteration failed to reach
    the model backend at all, as opposed to reaching it and getting a bad
    patch back. Feeds run_until's cross-iteration backoff (see modes.py) so
    a crashed local model server is not retried every iteration."""
    from .llm import is_backend_unavailable
    try:
        events = [json.loads(line) for line in
                  attempt.journal.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return False
    author_execs = [e for e in events if e.get("t") == "EXEC"
                    and str(e.get("action", "")).startswith("AuthorPatch")]
    if not author_execs:
        return False
    for event in author_execs:
        claims = [str(c) for c in event.get("claims", [])]
        if not claims or not all(is_backend_unavailable(c) for c in claims):
            return False
    return True


def _recent_failure_context(journal: Path) -> str:
    """Surface bounded, exact failure output to the next loop attempt."""
    try:
        lines = journal.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    failures = []
    for line in lines[-100:]:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("t") != "EXEC" or event.get("ok") is not False:
            continue
        parts = [str(event.get("action", "failed action")),
                 *[str(claim) for claim in event.get("claims", [])]]
        for command in event.get("commands", []):
            digest = command.get("out")
            if not isinstance(digest, str):
                continue
            blob = journal.parent / "blobs" / (digest + ".txt")
            try:
                output = blob.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parts.append(output[-2500:])
        failures.append("\n".join(parts))
    return "\n\n".join(failures[-2:])[-5000:]


def _last_accepted(journal: Path) -> bool:
    sidecar = journal.with_name("acceptance.jsonl")
    if not sidecar.exists():
        return False
    try:
        return json.loads(sidecar.read_text(encoding="utf-8").splitlines()[-1]
                          )["exit_code"] == 0
    except (IndexError, KeyError, ValueError, OSError):
        return False


# -- parser ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ledger",
        description="Audit-first coding agent: GATS plans, the LLM writes, "
                    "sensors verify, the journal proves it.")
    ap.add_argument("-V", "--version", action="version",
                    version=f"ledger {AGENT_VERSION}")
    # Not required: a bare `ledger` prints a welcome instead of an
    # argparse error listing eighteen subcommands.
    sub = ap.add_subparsers(dest="cmd")

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

    p = sub.add_parser("plan", help="write a comprehensive design document")
    p.add_argument("task")
    p.add_argument("--repo", default=".")
    p.add_argument("--out", default=None, help="new Markdown output path")
    add_backend(p)
    p.set_defaults(fn=cmd_plan_mode)

    p = sub.add_parser("browse", help="read a public website or documentation page")
    p.add_argument("url")
    p.add_argument("--max-chars", type=int, default=3500)
    p.set_defaults(fn=cmd_browse)

    p = sub.add_parser("research", help="propose a provisional domain result")
    p.add_argument("task")
    p.add_argument("--domain", required=True,
                   choices=["coding", "mathematics", "science", "reasoning"])
    p.add_argument("--repo", default=".")
    p.add_argument("--id", default=None, help="stable problem identifier")
    p.add_argument("--budget", type=int, default=100)
    p.add_argument("--contract", default="development-v1",
                   help="external evaluation contract identifier")
    p.add_argument("--input", action="append", help="input artifact reference")
    p.add_argument("--assumption", action="append")
    p.add_argument("--constraint", action="append")
    add_backend(p)
    p.set_defaults(fn=cmd_research_mode)

    p = sub.add_parser("brief", help=(
        "ask what a task actually requires, before building anything"))
    p.add_argument("task")
    p.add_argument("--repo", default=".")
    p.add_argument("--out", default=None, help="where to write the brief")
    p.add_argument("--kind", dest="product_kind", default=None,
                   choices=["game", "web", "cli", "service", "library",
                            "auto", "none"])
    add_backend(p)
    p.set_defaults(fn=cmd_brief)

    for mode in ("loop", "goal"):
        p = sub.add_parser(mode, help=(
            "refine repeatedly until Ctrl-C" if mode == "loop" else
            "repeat until an independent acceptance command succeeds"))
        p.add_argument("task")
        p.add_argument("--repo", default=".")
        p.add_argument("--name", default=None, help="persistent episode name")
        p.add_argument("--test", default=None)
        p.add_argument("--context", default=None,
                       help="comma-separated project files shown to the model")
        p.add_argument("--yes", action="store_true")
        p.add_argument("--no-research", action="store_true")
        p.add_argument("--self-heal", action="store_true",
                       help="repair a Ledger framework crash in an isolated "
                            "snapshot, verify it, and resume")
        p.add_argument("--author-timeout", type=int, default=None)
        p.add_argument("--max-iterations", type=int, default=None,
                       help="optional bound for this invocation")
        p.add_argument("--pause", type=float, default=0,
                       help="seconds between attempts (default: 0)")
        p.add_argument("--kind", dest="product_kind", default=None,
                       choices=["game", "web", "cli", "service", "library",
                                "auto", "none"],
                       help="what is being built (default: auto-detect from "
                            "the task). Its usability requirements are "
                            "inferred, reviewed once, probed on every "
                            "change, and put in front of the model")
        p.add_argument("--no-design-review", dest="design_review",
                       action="store_false", default=True,
                       help="do not photograph game/web builds for the "
                            "vision critique")
        if mode == "goal":
            p.add_argument("--accept", required=True,
                           help="trusted verifier command; candidate path is "
                                "appended as its final argument")
        add_backend(p)
        p.set_defaults(fn=cmd_iterative_mode)

    p = sub.add_parser("init", help="create .ledger/config.json in a repo")
    p.add_argument("--repo", default=".")
    p.add_argument("--test", default=None, help="test command, e.g. 'pytest -q'")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("run", help="run an agent episode against a repo")
    p.add_argument("--app-url", dest="app_url", default=None,
                   help="answer gates in a running `ledger app` "
                        "window instead of at the terminal "
                        "(env LEDGER_APP_URL)")
    p.add_argument("task", help="what you want done, in plain English")
    # Only fires when the goal is reached: launching a build already known
    # to be broken buries the escalation under a browser window.
    p.add_argument("--launch", nargs="?", const=True, default=None,
                   metavar="CMD",
                   help="on success, start what was built. With no argument, "
                        "detect it (npm start script, or serve index.html "
                        "and open a browser)")
    p.add_argument("--no-launch", dest="launch", action="store_false",
                   help="never launch, overriding config")
    p.add_argument("--repo", default=".")
    p.add_argument("--name", default="episode", help="episode name")
    p.add_argument("--out", default=None, help="episode directory")
    p.add_argument("--test", default=None, help="test command")
    p.add_argument("--slots", default=None,
                   help="comma-separated patch slots (default: a,b)")
    p.add_argument("--require-patch-slot", default=None,
                   help=argparse.SUPPRESS)
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
    p.add_argument("--author-timeout", dest="author_timeout", type=int,
                   default=None, metavar="SECONDS",
                   help="deadline for one authoring call; 0 means none. "
                        "Writing a whole app takes far longer than compiling "
                        "a goal, so this is separate from the backend timeout")
    p.add_argument("--strategise", "--strategize", dest="strategise",
                   action="store_true",
                   help="when nothing in the catalogue reaches the goal, "
                        "propose new actions instead of giving up. Proposals "
                        "must name sensor-verifiable effects and are gated "
                        "at the risk they declare")
    p.add_argument("--no-research", dest="research", action="store_false",
                   default=True,
                   help="do not search the web while strategising")
    p.add_argument("--kind", dest="product_kind", default=None,
                   choices=["game", "web", "cli", "service", "library",
                            "auto", "none"],
                   help="what is being built. Its usability requirements go "
                        "into the authoring prompt and are reviewed against "
                        "the contract (default: auto-detect from the task)")
    p.add_argument("--no-product-review", dest="product_review",
                   action="store_false", default=True,
                   help="skip the pre-flight 'is this usable' review")
    p.add_argument("--design-review", dest="design_review",
                   action="store_true",
                   help="photograph the running build and have a vision "
                        "model judge it against a rubric. Adds design.ok to "
                        "the goal, so the planner iterates on how it LOOKS")
    p.add_argument("--design-threshold", dest="design_threshold", type=float,
                   default=6.5, metavar="N",
                   help="mean rubric score out of 10 required (default 6.5)")
    p.add_argument("--design-model", dest="design_model", default=None,
                   metavar="MODEL",
                   help="vision model for the review (default qwen3-vl:32b)")
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
    p.add_argument("--app-url", dest="app_url", default=None,
                   help="answer gates in a running `ledger app` "
                        "window instead of at the terminal "
                        "(env LEDGER_APP_URL)")
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

    p = sub.add_parser(
        "setup", help="guided first-time setup: model backend, keys, speech")
    p.add_argument("--voice", dest="voice", action="store_true", default=None,
                   help="set up speech without being asked")
    p.add_argument("--no-voice", dest="voice", action="store_false",
                   help="skip the speech section")
    p.add_argument("--key", nargs="?", const=True, default=None,
                   metavar="PROVIDER",
                   help="just store an API key for PROVIDER and exit "
                        "(anthropic, openai, deepseek, groq)")
    p.add_argument("--forget", default=None, metavar="PROVIDER",
                   help="delete a stored API key")
    p.add_argument("--list-keys", dest="list_keys", action="store_true",
                   help="show which providers have a stored key, masked")
    p.set_defaults(fn=cmd_setup)

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

    p = sub.add_parser("receipt",
                       help="issue a signed, portable receipt for an episode")
    p.add_argument("journal", help="an episode directory, or any of its "
                                   "journal segments")
    p.add_argument("-o", "--out", help="where to write it "
                                       "(default: receipt.json beside the "
                                       "journal)")
    p.add_argument("--key", default="default",
                   help="signing key name under ~/.ledger/keys")
    p.add_argument("--run", action="store_true",
                   help="attest only the latest run of this one file, not "
                        "the whole episode")
    p.add_argument("--stdout", action="store_true",
                   help="write the receipt JSON to stdout instead of a file")
    p.set_defaults(fn=cmd_receipt)

    p = sub.add_parser("app",
                       help="the desktop control surface: watch a run, "
                            "approve its gates, read its receipt")
    p.add_argument("--repo", default=".")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8129)
    p.add_argument("--no-open", action="store_true",
                   help="do not open a browser (the Tauri shell opens its "
                        "own window)")
    p.set_defaults(fn=cmd_app)

    p = sub.add_parser("key",
                       help="show or export the public key receipts are "
                            "signed with")
    p.add_argument("--name", default="default")
    p.add_argument("--export", help="write {alg, key_id, public} to a file")
    p.set_defaults(fn=cmd_key)

    p = sub.add_parser("verify-receipt",
                       help="check a receipt's signature, and its journal "
                            "if you have it")
    p.add_argument("receipt")
    p.add_argument("--journal", help="the journal it claims to attest; "
                                     "without it only the signature is "
                                     "checked")
    p.add_argument("--key-id", help="the issuer key you expect, so a valid "
                                    "signature by a stranger still fails")
    p.set_defaults(fn=cmd_verify_receipt)

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

    p = sub.add_parser("rewind", help="list an episode's per-step snapshots, "
                                      "or restore the worktree to one")
    p.add_argument("episode", help="the episode directory")
    p.add_argument("--step", type=int, default=None,
                   help="restore the tree recorded before this step "
                        "(omit to list them)")
    p.set_defaults(fn=cmd_rewind)

    p = sub.add_parser("stats", help="show a persisted graph's statistics")
    p.add_argument("graph")
    p.set_defaults(fn=cmd_stats)
    return ap


def _welcome() -> int:
    """What a bare `ledger` says.

    It used to be an argparse error -- a wall of eighteen subcommand names
    under "the following arguments are required". That is the very first
    thing a new install prints, and it read as a fault rather than a
    greeting. Say the two commands that matter, and which one is next.
    """
    configured = bool(_config(Path(".").resolve()).get("backend"))
    print()
    print(ui.bold(f"LEDGER {AGENT_VERSION}") + ui.dim(
        "  -- a coding agent that can't lie about what it did"))
    print()
    if not configured:
        print("  Not set up yet. One command does it:")
        print()
        print("      " + ui.bold("ledger setup"))
        print(ui.dim("      the Claude CLI, an API key, or a local model "
                     "-- any one is enough"))
    else:
        # Padded on the plain text, not the styled string: ANSI escapes have
        # length but no width, so ljust on a coloured string misaligns by
        # exactly the number of invisible bytes in it.
        for cmd, blurb in (
                ("ledger chat", "ask, or describe a change -- one session "
                                "for everything"),
                ('ledger run "..."', "a single verified episode"),
                ("ledger doctor", "check this machine")):
            print("  " + ui.bold(cmd) + " " * (18 - len(cmd)) + ui.dim(blurb))
    print()
    print(ui.dim("  every command --help; full list: ledger -h"))
    print()
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) is None:
        return _welcome()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
