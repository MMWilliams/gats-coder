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
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .actions import Action, Catalog
from .goal import catalog_vocabulary, compile_goal
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


class BlobStore:
    """Content-addressed store for execution evidence (design SS9.1)."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def put(self, content: str) -> str:
        data = content.encode("utf-8", errors="replace")
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
            tail = (proc.stdout + proc.stderr)[-4000:]
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
    so the only safe answer is no."""
    if not sys.stdin.isatty():
        return False
    try:
        resp = input(f"[gate] allow {action_name} (risk={risk})? [y/N] ")
    except EOFError:
        return False
    return resp.strip().lower() in ("y", "yes")


class PolicyGate:
    """M4 risk gate: profile decides *which* risks stop, an approver decides
    *whether* they proceed. The answer for an action family (`AuthorPatch`,
    not `AuthorPatch:a`) is remembered for the episode, so a plan that visits
    the same action ten times asks once.
    """

    def __init__(self, profile: str = "supervised",
                 approver: Gate = tty_approver):
        if profile not in GATED_RISKS:
            raise ValueError(f"unknown profile {profile!r} "
                             f"(choose from {sorted(GATED_RISKS)})")
        self.profile = profile
        self.approver = approver
        self.memo: dict[str, bool] = {}

    def __call__(self, action_name: str, risk: str) -> bool:
        if risk not in GATED_RISKS[self.profile]:
            return True
        family = action_name.split(":", 1)[0]
        if family not in self.memo:
            self.memo[family] = self.approver(action_name, risk)
        return self.memo[family]


class RepoExecutor:
    def __init__(self, ctx: ExecContext, catalog: Catalog,
                 skills: dict[str, Skill], sensors: SensorBank,
                 journal: Journal | None = None,
                 gate: Gate = attended_gate, profile: str = "attended"):
        self.ctx = ctx
        self.catalog = catalog
        self.skills = skills
        self.sensors = sensors
        self.journal = journal
        self.gate = gate
        self.profile = profile
        self.incidents = 0
        self.denials = 0

    def env_step(self, s: Props, action_name: str) -> Props:
        action = self.catalog.get(action_name)
        if action is None:
            raise KeyError(f"no catalog entry for {action_name!r}")

        if action.risk != "read":
            approved = self.gate(action_name, action.risk)
            if self.journal is not None:
                self.journal.append("GATE", action=action_name,
                                    risk=action.risk, approved=approved,
                                    profile=self.profile)
            if not approved:
                # SS8.1: a denial is an observation, not a crash. The state
                # does not change, the divergence arm learns the self-loop,
                # and useful_actions() prunes the arm from future search --
                # the planner routes around the human's "no".
                self.denials += 1
                return self.sensors.probe(self.ctx.worktree)

        result = self.skills[action_name].run(self.ctx)
        if self.journal is not None:
            self.journal.append(
                "EXEC", action=action_name, ok=result.ok,
                claims=list(result.claims),
                claimed_props=sorted(result.claimed_props),
                commands=result.commands, artifacts=result.artifacts,
                risk=action.risk,
            )

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

    goal_extra: dict = {}
    if task_text is not None:
        if backend is None:
            raise ValueError("task_text requires an LLM backend")
        contract = compile_goal(task_text, catalog_vocabulary(catalog), backend)
        goal = contract.goal
        goal_extra = {"task_text": contract.task_text,
                      "goal_rationale": contract.rationale,
                      "goal_backend": contract.backend_id}
    if goal is None:
        raise ValueError("either goal or task_text must be provided")

    worktree = Worktree.add(repo_root, episode_dir / "worktree",
                            branch=f"ledger/{episode_dir.name}")
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
    sensors = SensorBank(sensor_list)
    ctx = ExecContext(worktree=worktree.path,
                      blobs=BlobStore(episode_dir / "blobs"),
                      test_cache=test_cache,
                      queue_dir=queue_dir, applied_dir=applied_dir)
    skills: dict[str, Skill] = {
        "RunSuite": RunSuiteSkill(test_cmd),
        "Commit": CommitSkill("ledger: apply queued fix"),
        **{f"ApplyPatch:{pid}": ApplyPatchSkill(pid) for pid in patch_ids},
    }
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

    journal_path = episode_dir / "episode.jsonl"
    with Journal(str(journal_path)) as journal:
        executor = RepoExecutor(ctx, catalog, skills, sensors,
                                journal=journal, gate=gate, profile=profile)
        task = Task("repo-episode", sensors.probe(worktree.path), goal, catalog)
        graph = TransitionGraph()
        wm = LayeredWorldModel(catalog, l3=l3)
        if l2_path and Path(l2_path).exists():
            wm.l2.load(l2_path)
        result = run_episode(
            task, graph, wm, cfg, executor.env_step, journal=journal,
            header_extra={"sensor_set_hash": sensors.set_hash(),
                          "profile": executor.profile,
                          "branch": worktree.branch, **goal_extra},
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
