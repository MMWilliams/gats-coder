"""Skill executor and sandboxed repo-episode runner (design SS5.2, SS8, SS9).

Skills do the actual work inside plan-level actions. Their output is
*proposed, never believed*: after every execution the sensor bank re-probes
the worktree, and only sensor propositions enter state. A skill claim not
supported by the sensors becomes a journaled INCIDENT (SS9.4) -- the
lie-detection invariant.

Journal ordering per step: PLAN (planner) -> GATE -> EXEC [-> INCIDENT]
(executor, inside env_step) -> OBSERVE (planner).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .actions import Action, Catalog
from .goal import GoalCompileError, compile_goal, goal_vocabulary
from .graph import TransitionGraph
from .journal import Journal
from .planner import PlanConfig, EpisodeResult, Task, run_episode
from .repo import Worktree, content_fingerprint, git
from .sensors import (ExpensiveResultCache, SensorBank, dir_marker_sensor,
                      git_sensor, test_status_sensor)
from .state import Props
from .world_model import L3Generative, LayeredWorldModel


class GateDenied(RuntimeError):
    pass


class BackendUnavailable(RuntimeError):
    """The model backend could not be reached at all. Distinct from a
    backend that answers badly: nothing can start, so the caller should
    say so plainly rather than print a stack trace."""


class BudgetMeter:
    """Per-episode spend counter for a metered resource (design SS12.3).

    Skills charge it; a sensor reads it. Exhaustion is surfaced as the
    *absence* of the resource proposition, which makes budget-gated actions
    symbolically inapplicable -- the planner routes around them or the
    episode ends in escalation. No exceptions, no hidden retries.
    """

    def __init__(self, limit: int, resource: str = "llm"):
        self.limit = int(limit)
        self.resource = resource
        self.spent = 0

    def charge(self, n: int = 1) -> None:
        self.spent += n

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit

    @property
    def prop(self) -> str:
        return f"budget.{self.resource}:ok"


# Credential shapes scrubbed from journaled evidence. The journal is meant
# to be shared (bug reports, audits); tokens that leak into command output
# must not outlive the run.
_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
                r"-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r"(?i)(bearer\s+)[a-z0-9_.=+/-]{16,}"), r"\1[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|auth[_-]?token|"
                r"secret|password|passwd)\s*[=:]\s*)[^\s'\"]{8,}"),
     r"\1[REDACTED]"),
]


def redact_secrets(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class BlobStore:
    """Content-addressed store for execution evidence (design SS9.1).
    Content is secret-redacted before hashing, so the stored blob and its
    address agree and neither retains credentials."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def put(self, content: str) -> str:
        data = redact_secrets(content).encode("utf-8", errors="replace")
        sha = hashlib.sha256(data).hexdigest()
        path = self.directory / f"{sha}.txt"
        if not path.exists():
            path.write_bytes(data)
        return sha


@dataclass
class SkillResult:
    ok: bool
    claimed_props: frozenset = frozenset()  # checked against sensors
    claims: list = field(default_factory=list)  # freeform, recorded not believed
    commands: list = field(default_factory=list)  # [{cmd, exit, out}]
    artifacts: dict = field(default_factory=dict)  # label -> blob sha


@dataclass
class ExecContext:
    worktree: Path
    blobs: BlobStore
    test_cache: ExpensiveResultCache
    queue_dir: Path
    applied_dir: Path
    # Most recent failing suite output (tail), deposited by RunSuiteSkill and
    # read by AuthorPatchSkill so the model patches against real evidence.
    suite_evidence: dict = field(default_factory=dict)
    # Optional (text) -> None sink for live phase reporting. Skills call it
    # so a UI can say what is happening *now* instead of "working...".
    progress: object = None

    def say(self, text: str) -> None:
        if self.progress is not None:
            try:
                self.progress(text)
            except Exception:
                pass  # a broken display must never fail an episode


class Skill(Protocol):
    def run(self, ctx: ExecContext) -> SkillResult: ...


def _run_cmd(ctx: ExecContext, cmd: list[str]) -> tuple[subprocess.CompletedProcess, dict]:
    proc = subprocess.run(cmd, cwd=ctx.worktree, capture_output=True,
                          text=True, timeout=600)
    out_sha = ctx.blobs.put(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    return proc, {"cmd": " ".join(cmd), "exit": proc.returncode, "out": out_sha}


def fail_bucket(n: int) -> str:
    return "1-5" if n <= 5 else "6+"


class RunSuiteSkill:
    """Runs the configured test command; deposits the sensor-readable result
    into the fingerprint-keyed cache (the sensor never guesses)."""

    def __init__(self, cmd: list[str], suite: str = "unit"):
        self.cmd = cmd
        self.suite = suite

    def run(self, ctx: ExecContext) -> SkillResult:
        ctx.say(f"running the {self.suite} suite")
        proc, rec = _run_cmd(ctx, self.cmd)
        if proc.returncode == 0:
            props = {f"tests.pass:{self.suite}"}
            claims = [f"{self.suite} suite passes"]
            ctx.suite_evidence.pop(self.suite, None)
        else:
            m = re.search(r"(\d+) failed", proc.stdout)
            bucket = fail_bucket(int(m.group(1))) if m else "1-5"
            props = {f"tests.fail_count:{self.suite}:{bucket}"}
            claims = [f"{self.suite} suite failing ({bucket})"]
            tail = redact_secrets((proc.stdout + proc.stderr)[-4000:])
            ctx.suite_evidence[self.suite] = {"tail": tail, "sha": rec["out"]}
        ctx.test_cache.put(content_fingerprint(ctx.worktree), props)
        return SkillResult(ok=proc.returncode == 0,
                           claimed_props=frozenset(props),
                           claims=claims, commands=[rec],
                           artifacts={"suite_output": rec["out"]})


class ApplyPatchSkill:
    def __init__(self, patch_id: str):
        self.patch_id = patch_id

    def run(self, ctx: ExecContext) -> SkillResult:
        patch_file = ctx.queue_dir / f"{self.patch_id}.patch"
        proc, rec = _run_cmd(ctx, ["git", "apply", str(patch_file)])
        if proc.returncode != 0:
            return SkillResult(ok=False, claims=["patch failed to apply"],
                               commands=[rec])
        ctx.applied_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(patch_file), str(ctx.applied_dir / patch_file.name))
        diff_sha = ctx.blobs.put(
            (ctx.applied_dir / patch_file.name).read_text(encoding="utf-8"))
        return SkillResult(
            ok=True,
            claimed_props=frozenset({f"patch.applied:{self.patch_id}"}),
            claims=[f"applied patch {self.patch_id}"],
            commands=[rec], artifacts={"diff": diff_sha})


class CommitSkill:
    def __init__(self, message: str):
        self.message = message

    def run(self, ctx: ExecContext) -> SkillResult:
        recs = []
        for cmd in (["git", "add", "-A"],
                    ["git", "-c", "user.name=ledger",
                     "-c", "user.email=ledger@local",
                     "commit", "-m", self.message]):
            proc, rec = _run_cmd(ctx, cmd)
            recs.append(rec)
            if proc.returncode != 0:
                return SkillResult(ok=False, claims=["commit failed"],
                                   commands=recs)
        return SkillResult(ok=True, claimed_props=frozenset({"repo.clean"}),
                           claims=[f"committed: {self.message}"], commands=recs)


class PushBranchSkill:
    """Pushes the episode branch to origin. risk='network': supervised and
    attended profiles stop here — publishing is the boundary where a wrong
    patch stops being free to undo."""

    def __init__(self, branch: str):
        self.branch = branch

    def run(self, ctx: ExecContext) -> SkillResult:
        proc, rec = _run_cmd(ctx, ["git", "push", "origin", self.branch])
        if proc.returncode != 0:
            return SkillResult(ok=False, claims=["push failed"],
                               commands=[rec])
        return SkillResult(
            ok=True,
            claimed_props=frozenset({f"repo.pushed:{self.branch}"}),
            claims=[f"pushed {self.branch} to origin"], commands=[rec])


class CreatePRSkill:
    """Opens a pull request for the pushed episode branch via the `gh` CLI.
    risk='network'. The claim is `repo.pr:<branch>`; the sensor confirms it
    by asking `gh pr view`, not by trusting this exit code."""

    def __init__(self, branch: str, title: str, body: str = ""):
        self.branch = branch
        self.title = title[:120] or branch
        self.body = body
        self.gh = shutil.which("gh")

    def run(self, ctx: ExecContext) -> SkillResult:
        if self.gh is None:
            return SkillResult(ok=False,
                               claims=["gh CLI not found on PATH"])
        proc, rec = _run_cmd(ctx, [
            self.gh, "pr", "create", "--head", self.branch,
            "--title", self.title,
            "--body", self.body or f"LEDGER episode branch `{self.branch}`."])
        if proc.returncode != 0:
            return SkillResult(ok=False, claims=["pr create failed"],
                               commands=[rec])
        return SkillResult(
            ok=True,
            claimed_props=frozenset({f"repo.pr:{self.branch}"}),
            claims=[f"opened PR for {self.branch}"], commands=[rec])


# gate(action_name, risk) -> bool (approve). Design SS8.1: gates fire at
# execution, never during search.
Gate = Callable[[str, str], bool]


def attended_gate(action_name: str, risk: str) -> bool:
    """Approve everything (used by tests and unattended callers that opt in
    explicitly, e.g. `ledger run --yes`)."""
    return True


# Which risk classes each autonomy profile stops on (design SS7.4).
GATED_RISKS = {
    "attended": frozenset({"write", "exec", "network", "irreversible"}),
    "supervised": frozenset({"network", "irreversible"}),
    "autonomous": frozenset({"irreversible"}),
}


def tty_approver(action_name: str, risk: str) -> bool:
    """Ask the human at the terminal. Without a terminal there is no human,
    so the only safe answer is no.

    The working indicator is suspended first: it repaints the line every
    80ms, and a question it paints over is a question nobody can answer --
    the session then looks hung while it waits forever.
    """
    if not sys.stdin.isatty():
        return False
    from . import tui, ui as _ui
    try:
        with tui.paused():
            resp = input(_ui.warn(
                f"[gate] allow {action_name} (risk={risk})? [y/N] "))
    except (EOFError, OSError):
        return False
    return resp.strip().lower() in ("y", "yes")


class PolicyGate:
    """M4 risk gate: profile decides *which* risks stop, an approver decides
    *whether* they proceed. The answer for an action family (`AuthorPatch`,
    not `AuthorPatch:a`) is remembered for the episode, so a plan that visits
    the same action ten times asks once.

    `rules` (execpolicy / permission-list analog) is standing policy over
    action families: {"deny": [...], "allow": [...]}. Deny always wins --
    over the profile, over the approver, and over --yes; allow pre-approves
    a family the profile would otherwise stop on. Rules are policy, prompts
    are consent; policy outranks consent.
    """

    def __init__(self, profile: str = "supervised",
                 approver: Gate = tty_approver,
                 rules: dict | list | None = None):
        if profile not in GATED_RISKS:
            raise ValueError(f"unknown profile {profile!r} "
                             f"(choose from {sorted(GATED_RISKS)})")
        self.profile = profile
        self.approver = approver
        self.rules = self._normalize(rules)
        self.memo: dict[str, bool] = {}
        # Why the last decision came out the way it did; journaled by the
        # executor next to the GATE entry (audit trail, Kimi-rule style).
        self.last_reason: str | None = None

    @staticmethod
    def _normalize(rules) -> list[tuple[str, str, str | None]]:
        """Accepts the set form {"deny": [...], "allow": [...]} (deny wins)
        or the ordered form [{"action": ..., "decision": allow|deny|ask,
        "reason": ...}] (first match wins). Returns an ordered rule list."""
        if not rules:
            return []
        if isinstance(rules, dict) and "rules" in rules:
            rules = rules["rules"]
        if isinstance(rules, dict):
            out = [(str(f), "deny", None) for f in rules.get("deny", ())]
            out += [(str(f), "allow", None) for f in rules.get("allow", ())]
            return out
        out = []
        for r in rules:
            decision = r.get("decision")
            if decision not in ("allow", "deny", "ask"):
                raise ValueError(f"rule decision must be allow/deny/ask: {r}")
            out.append((str(r.get("action", "*")), decision,
                        r.get("reason")))
        return out

    def _match(self, action_name: str, family: str):
        for pattern, decision, reason in self.rules:
            if pattern in ("*", family, action_name):
                return decision, reason
        return None, None

    def __call__(self, action_name: str, risk: str) -> bool:
        family = action_name.split(":", 1)[0]
        self.last_reason = None
        decision, reason = self._match(action_name, family)
        if decision == "deny":
            self.last_reason = reason or "denied by policy rule"
            return False
        if decision == "allow":
            self.last_reason = reason
            return True
        if decision != "ask" and risk not in GATED_RISKS[self.profile]:
            return True
        if family not in self.memo:
            self.memo[family] = self.approver(action_name, risk)
        return self.memo[family]


class RepoExecutor:
    def __init__(self, ctx: ExecContext, catalog: Catalog,
                 skills: dict[str, Skill], sensors: SensorBank,
                 journal: Journal | None = None,
                 gate: Gate = attended_gate, profile: str = "attended",
                 hooks: list[dict] | None = None):
        self.ctx = ctx
        self.catalog = catalog
        self.skills = skills
        self.sensors = sensors
        self.journal = journal
        self.gate = gate
        self.profile = profile
        self.hooks = list(hooks or [])
        self.incidents = 0
        self.denials = 0
        self.steps = 0

    # -- hooks (Claude Code / Kimi hook engine analog) -----------------------
    # Deterministic commands around actions, journaled like everything else.
    # A pre_action hook with "gate": true blocks the action on nonzero exit
    # (fail closed -- a gate that cannot run is a gate that says no).

    @staticmethod
    def _hook_matches(hook: dict, action_name: str, family: str) -> bool:
        m = str(hook.get("match", "*"))
        return m in ("*", family, action_name)

    def _run_hook(self, hook: dict, event: str, action_name: str,
                  risk: str) -> int:
        cmd = [str(c) for c in hook.get("command", [])]
        if not cmd:
            return -1
        try:
            proc = subprocess.run(
                cmd, cwd=self.ctx.worktree, capture_output=True, text=True,
                timeout=int(hook.get("timeout", 30)),
                env={**os.environ,
                     "LEDGER_EVENT": event,
                     "LEDGER_ACTION": action_name,
                     "LEDGER_RISK": risk,
                     "LEDGER_WORKTREE": str(self.ctx.worktree)})
            exit_code = proc.returncode
            out_sha = self.ctx.blobs.put(
                proc.stdout + "\n--- stderr ---\n" + proc.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            exit_code = -1
            out_sha = self.ctx.blobs.put(f"hook failed to run: {exc}")
        if self.journal is not None:
            self.journal.append(
                "HOOK", event=event, action=action_name,
                match=str(hook.get("match", "*")), cmd=" ".join(cmd),
                exit=exit_code, out=out_sha,
                gate=bool(hook.get("gate")),
                blocked=bool(hook.get("gate")) and exit_code != 0)
        return exit_code

    def _fire_hooks(self, event: str, action_name: str, risk: str) -> bool:
        """Run matching hooks; returns True if a gate hook blocks."""
        family = action_name.split(":", 1)[0]
        blocked = False
        for hook in self.hooks:
            if hook.get("event", "pre_action") != event:
                continue
            if not self._hook_matches(hook, action_name, family):
                continue
            exit_code = self._run_hook(hook, event, action_name, risk)
            if event == "pre_action" and hook.get("gate") and exit_code != 0:
                blocked = True
        return blocked

    def env_step(self, s: Props, action_name: str) -> Props:
        action = self.catalog.get(action_name)
        if action is None:
            raise KeyError(f"no catalog entry for {action_name!r}")

        if action.risk != "read":
            approved = self.gate(action_name, action.risk)
            reason = getattr(self.gate, "last_reason", None)
            if self.journal is not None:
                self.journal.append("GATE", action=action_name,
                                    risk=action.risk, approved=approved,
                                    profile=self.profile, reason=reason)
            if not approved:
                # SS8.1: a denial is an observation, not a crash. The state
                # does not change, the divergence arm learns the self-loop,
                # and useful_actions() prunes the arm from future search --
                # the planner routes around the human's "no".
                self.denials += 1
                return self.sensors.probe(self.ctx.worktree)

        if self._fire_hooks("pre_action", action_name, action.risk):
            self.denials += 1
            return self.sensors.probe(self.ctx.worktree)

        self.ctx.say(f"step {self.steps}: {action_name}")
        self.steps += 1
        result = self.skills[action_name].run(self.ctx)
        if self.journal is not None:
            self.journal.append(
                "EXEC", action=action_name, ok=result.ok,
                claims=list(result.claims),
                claimed_props=sorted(result.claimed_props),
                commands=result.commands, artifacts=result.artifacts,
                risk=action.risk,
            )
        self._fire_hooks("post_action", action_name, action.risk)

        obs = self.sensors.probe(self.ctx.worktree)
        false_claims = sorted(set(result.claimed_props) - obs)
        if false_claims:
            self.incidents += 1
            if self.journal is not None:
                self.journal.append("INCIDENT", type="false_claim",
                                    action=action_name,
                                    claimed_not_observed=false_claims)
        return obs


TEST_FAIL_PROPS = ("tests.fail_count:unit:1-5", "tests.fail_count:unit:6+")


def build_coding_catalog(patch_ids: list[str], suite: str = "unit") -> Catalog:
    """The M2 action catalog (design SS5.1 subset): RunSuite (stochastic,
    optimistic contract), ApplyPatch per queued patch, Commit."""
    fails = tuple(p.replace("unit", suite) for p in TEST_FAIL_PROPS)
    pass_prop = f"tests.pass:{suite}"
    actions = [
        Action("RunSuite", prec=frozenset(),
               add=frozenset({pass_prop}), delete=frozenset(fails),
               risk="exec", deterministic=False),
        Action("Commit",
               prec=frozenset({pass_prop})
               | {f"patch.applied:{pid}" for pid in patch_ids},
               add=frozenset({"repo.clean"}), risk="write"),
    ]
    for pid in patch_ids:
        actions.append(Action(
            f"ApplyPatch:{pid}",
            prec=frozenset({f"patch.queued:{pid}"}),
            add=frozenset({f"patch.applied:{pid}"}),
            delete=frozenset({f"patch.queued:{pid}", "repo.clean",
                              pass_prop, *fails}),
            risk="write"))
    return Catalog(actions)


def _protect_scratch(repo_root: Path) -> None:
    """Keep episode scratch out of the user's history.

    Episodes write worktrees, blobs and sqlite into .ledger/. A single
    `git add -A` would otherwise sweep an embedded worktree into their
    repository, which is confusing to undo. `ledger init` wrote this file;
    an episode started without init did not, so write it here where every
    path goes through.
    """
    d = repo_root / ".ledger"
    d.mkdir(exist_ok=True)
    ignore = d / ".gitignore"
    if not ignore.exists():
        ignore.write_text("episodes/\nsessions/\n*.sqlite\n",
                          encoding="utf-8")


def run_repo_episode(repo_root: Path | str, episode_dir: Path | str,
                     goal: Props | None, test_cmd: list[str],
                     cfg: PlanConfig | None = None,
                     gate: Gate = attended_gate,
                     skill_overrides: dict[str, Skill] | None = None,
                     task_text: str | None = None,
                     backend=None,
                     l2_path: str | None = None,
                     l3_cache_path: str | None = None,
                     slots: list[str] | None = None,
                     context_files: list[str] | None = None,
                     profile: str = "attended",
                     max_author_attempts: int = 6,
                     push: bool = False,
                     pr: bool = False,
                     resume: bool = False,
                     hooks: list[dict] | None = None,
                     progress=None,
                     seed_graph_path: str | None = None,
                     ) -> tuple[EpisodeResult, RepoExecutor, Path]:
    """End-to-end journaled episode on a real repo (M2/M3).

    Expects episode_dir/queue to be pre-populated with <id>.patch files
    (patch authoring is the M4+ LLM skill's job). Pass either an explicit
    `goal` or a natural-language `task_text` + `backend` -- the goal
    compiler then derives (and vocabulary-validates) the goal contract,
    which lands in the journal HEADER. `l2_path`/`l3_cache_path` persist
    the learned layers across episodes (loaded if present, saved after).
    Returns the episode result, the executor (incident count), and the
    journal path. The worktree is left in place for attended inspection.
    """
    repo_root, episode_dir = Path(repo_root), Path(episode_dir)
    cfg = cfg or PlanConfig()
    _protect_scratch(repo_root)
    queue_dir = episode_dir / "queue"
    applied_dir = episode_dir / "applied"
    queue_dir.mkdir(parents=True, exist_ok=True)

    patch_ids = sorted(f.stem for f in queue_dir.glob("*.patch"))
    if slots:
        # Lazy: skills_llm imports from this module.
        from .skills_llm import AuthorPatchSkill, build_authoring_catalog
        catalog = build_authoring_catalog(sorted(slots))
    else:
        catalog = build_coding_catalog(patch_ids)

    branch = f"ledger/{episode_dir.name}"
    push = push or pr
    extra_actions = []
    if push:
        # Publishing is a separate, network-risk action: reachable only from
        # a clean (committed) worktree, gated under attended/supervised.
        extra_actions.append(Action(
            "PushBranch",
            prec=frozenset({"repo.clean"}),
            add=frozenset({f"repo.pushed:{branch}"}),
            risk="network", deterministic=False))
    if pr:
        extra_actions.append(Action(
            "CreatePR",
            prec=frozenset({f"repo.pushed:{branch}"}),
            add=frozenset({f"repo.pr:{branch}"}),
            risk="network", deterministic=False))
    if extra_actions:
        catalog = Catalog(list(catalog) + extra_actions)

    def say(text: str) -> None:
        if progress is not None:
            try:
                progress(text)
            except Exception:
                pass

    goal_extra: dict = {}
    if task_text is not None:
        if backend is None:
            raise ValueError("task_text requires an LLM backend")
        say("compiling the goal contract")
        try:
            contract = compile_goal(task_text, goal_vocabulary(catalog),
                                    backend)
        except GoalCompileError:
            raise
        except RuntimeError as exc:
            # A dead backend here is a configuration problem, not a bug in
            # the episode. Say which backend failed and how to switch,
            # instead of a traceback through the planner.
            raise BackendUnavailable(
                f"the {getattr(backend, 'id', 'configured')} backend failed "
                f"before the episode began: {exc}\n"
                "  try another one, e.g. --backend ollama --model "
                "<local-model>, --backend anthropic (ANTHROPIC_API_KEY), "
                "or --backend openai --model <model>") from exc
        goal = contract.goal
        goal_extra = {"task_text": contract.task_text,
                      "goal_rationale": contract.rationale,
                      "goal_backend": contract.backend_id}
    if goal is None:
        raise ValueError("either goal or task_text must be provided")

    worktree_dir = episode_dir / "worktree"
    # An existing worktree must be re-entered, never re-created: git refuses
    # to add one where a directory already stands. `resume` decides whether
    # the graph warms up and the journal segments -- not whether the
    # worktree can be reused, which is a fact about the filesystem.
    if (worktree_dir / ".git").exists():
        worktree = Worktree.reuse(repo_root, worktree_dir, branch=branch)
    else:
        worktree = Worktree.add(repo_root, worktree_dir, branch=branch)
    test_cache = ExpensiveResultCache()
    sensor_list = [
        git_sensor(),
        test_status_sensor(test_cache, content_fingerprint),
        dir_marker_sensor("queue", "patch.queued:", queue_dir),
        dir_marker_sensor("applied", "patch.applied:", applied_dir),
    ]
    meter = None
    if slots:
        from .sensors import budget_sensor
        meter = BudgetMeter(max_author_attempts, resource="llm")
        sensor_list.append(budget_sensor(meter))
    if push:
        from .sensors import remote_branch_sensor
        sensor_list.append(remote_branch_sensor(branch))
    if pr:
        from .sensors import pr_sensor
        sensor_list.append(pr_sensor(branch))
    sensors = SensorBank(sensor_list)
    ctx = ExecContext(worktree=worktree.path,
                      blobs=BlobStore(episode_dir / "blobs"),
                      test_cache=test_cache,
                      queue_dir=queue_dir, applied_dir=applied_dir,
                      progress=progress)
    skills: dict[str, Skill] = {
        "RunSuite": RunSuiteSkill(test_cmd),
        "Commit": CommitSkill("ledger: apply queued fix"),
        **{f"ApplyPatch:{pid}": ApplyPatchSkill(pid) for pid in patch_ids},
    }
    if push:
        skills["PushBranch"] = PushBranchSkill(branch)
    if pr:
        skills["CreatePR"] = CreatePRSkill(branch, title=task_text or branch)
    if slots:
        for slot in sorted(slots):
            skills[f"AuthorPatch:{slot}"] = AuthorPatchSkill(
                slot, backend, task_text or "", context_files, meter=meter)
            skills[f"ApplyPatch:{slot}"] = ApplyPatchSkill(slot)
    skills.update(skill_overrides or {})

    l3 = None
    if backend is not None:
        l3 = L3Generative(
            backend,
            cache_path=l3_cache_path
            if l3_cache_path and Path(l3_cache_path).exists() else None)

    # Resume appends a fresh journal *segment* (episode.r2.jsonl, ...): each
    # file stays an independently verifiable, independently replayable chain.
    journal_path = episode_dir / "episode.jsonl"
    segment = 1
    if resume:
        while journal_path.exists():
            segment += 1
            journal_path = episode_dir / f"episode.r{segment}.jsonl"
    with Journal(str(journal_path)) as journal:
        executor = RepoExecutor(ctx, catalog, skills, sensors,
                                journal=journal, gate=gate, profile=profile,
                                hooks=hooks)
        task = Task("repo-episode", sensors.probe(worktree.path), goal, catalog)
        graph_path = episode_dir / "graph.sqlite"
        if resume and graph_path.exists():
            # Warm start: the prior segment's transition graph -- learned
            # edges, UCB statistics, dead ends -- carries over.
            graph = TransitionGraph.load(str(graph_path))
        elif seed_graph_path and Path(seed_graph_path).exists():
            # POOLED start (paper SS3.3): begin from a graph other agents
            # already verified. Distinct from --resume, which continues THIS
            # episode; this inherits structure from a *population*.
            #
            # The seeded edges carry their UCB statistics, so an arm another
            # agent already sampled starts visited rather than at +inf, and
            # search spends its budget on what is genuinely unknown here.
            # Determinism is unaffected: the seed is an input like any other,
            # and replay pins it by recording the seed's stats in the HEADER.
            graph = TransitionGraph.load(str(seed_graph_path))
        else:
            graph = TransitionGraph()
        seeded = graph.stats()
        wm = LayeredWorldModel(catalog, l3=l3)
        if l2_path and Path(l2_path).exists():
            wm.l2.load(l2_path)
        result = run_episode(
            task, graph, wm, cfg, executor.env_step, journal=journal,
            header_extra={"sensor_set_hash": sensors.set_hash(),
                          "profile": executor.profile,
                          "branch": worktree.branch,
                          "segment": segment, "resumed": bool(resume),
                          "seeded_graph": seeded,
                          **goal_extra},
        )
        if not result.success:
            # SS7.4 escalation: the episode could not reach the goal on its
            # own. Journal *why* so the human resumes from evidence, not
            # from a shrug.
            if meter is not None and meter.exhausted:
                reason = "budget_exhausted"
            elif executor.denials:
                reason = "gate_denied"
            elif result.steps >= cfg.max_steps:
                reason = "max_steps"
            else:
                reason = "no_path"
            journal.append(
                "ESCALATION", reason=reason, denials=executor.denials,
                author_attempts=(meter.spent if meter is not None else 0),
                author_attempt_limit=(meter.limit if meter is not None else 0),
            )
        graph.save(str(episode_dir / "graph.sqlite"))
        if l2_path:
            wm.l2.save(l2_path)
        if l3_cache_path and l3 is not None:
            l3.save(l3_cache_path)
    return result, executor, journal_path
