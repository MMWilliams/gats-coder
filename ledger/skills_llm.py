"""LLM-authored patches: the skill that lets LEDGER write code (design SS5.2).

The planner still never sees tokens. `AuthorPatch:<slot>` is an ordinary
plan-level action whose contract says "a patch becomes queued in this slot";
inside it, an LLM synthesizes a unified diff. The diff is *proposed, never
believed*: it is validated with `git apply --check` (deterministic) before it
is allowed into the queue, and the queue directory is read back by the
dir_marker sensor -- so the proposition that enters state comes from the
filesystem, not from the model.

Slots exist because the catalog must be fixed (and content-hashed) before the
episode starts, while authored patches appear during it. A fixed slot set
keeps |A_app| inside the BFS envelope (design SS5.1) and keeps
`catalog_hash` meaningful for replay.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .actions import Action, Catalog
from .executor import SkillResult, TEST_FAIL_PROPS, _run_cmd

_DIFF_START = re.compile(r"^(diff --git |--- |\+\+\+ )", re.MULTILINE)
_FENCE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)

PATCH_PROMPT = """You are writing a patch for a git repository.

TASK
{task}

REPOSITORY FILES
{tree}

{extra}

Reply with a single unified diff and NOTHING else -- no prose, no explanation,
no code fence. The diff must apply cleanly with `git apply` from the
repository root. Use paths relative to the root with the standard a/ and b/
prefixes. Do not modify test files unless the task explicitly asks you to.
"""


def repo_tree(root: Path, max_files: int = 200) -> str:
    """Deterministic file listing for prompt context (tracked files only)."""
    proc = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                          text=True, timeout=60)
    files = sorted(proc.stdout.split())
    shown = files[:max_files]
    out = "\n".join(shown)
    if len(files) > max_files:
        out += f"\n... and {len(files) - max_files} more"
    return out


def read_files(root: Path, rel_paths: list[str], max_bytes: int = 20000) -> str:
    """Inline a bounded set of files into the prompt, deterministically."""
    chunks = []
    budget = max_bytes
    for rel in sorted(rel_paths):
        f = root / rel
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = text[:budget]
        budget -= len(text)
        chunks.append(f"--- {rel} ---\n{text}")
        if budget <= 0:
            break
    return "\n\n".join(chunks)


def extract_diff(text: str) -> str | None:
    """Pull a unified diff out of a model response, tolerating code fences
    and leading prose. Returns None when nothing diff-shaped is present."""
    fenced = _FENCE.search(text)
    if fenced:
        candidate = fenced.group(1)
        if _DIFF_START.search(candidate):
            return candidate if candidate.endswith("\n") else candidate + "\n"
    m = _DIFF_START.search(text)
    if m is None:
        return None
    candidate = text[m.start():]
    # Trim a trailing fence if the model wrapped the tail only.
    candidate = candidate.split("```")[0]
    return candidate if candidate.endswith("\n") else candidate + "\n"


@dataclass
class PatchAttempt:
    ok: bool
    reason: str = ""
    diff: str = ""


class AuthorPatchSkill:
    """Synthesizes a unified diff with an LLM and queues it in `slot`.

    Verification before queuing (all deterministic, none of it trusts the
    model): the response must contain a diff, and `git apply --check` must
    accept it against the live worktree. A rejected diff produces
    ok=False with no queued proposition -- the planner sees the failure as a
    normal non-effect and re-plans.
    """

    def __init__(self, slot: str, backend, task_text: str,
                 context_files: list[str] | None = None,
                 max_attempts: int = 2):
        self.slot = slot
        self.backend = backend
        self.task_text = task_text
        self.context_files = list(context_files or [])
        self.max_attempts = max_attempts

    def _prompt(self, ctx, failure: str = "") -> str:
        extra_parts = []
        if self.context_files:
            extra_parts.append("RELEVANT FILE CONTENTS\n"
                               + read_files(ctx.worktree, self.context_files))
        if failure:
            extra_parts.append(
                "YOUR PREVIOUS ATTEMPT WAS REJECTED\n" + failure
                + "\nProduce a corrected diff.")
        return PATCH_PROMPT.format(
            task=self.task_text,
            tree=repo_tree(ctx.worktree),
            extra="\n\n".join(extra_parts),
        )

    def _validate(self, ctx, diff: str) -> PatchAttempt:
        tmp = ctx.queue_dir / f".{self.slot}.candidate"
        tmp.write_text(diff, encoding="utf-8", newline="\n")
        proc = subprocess.run(
            ["git", "apply", "--check", str(tmp)], cwd=ctx.worktree,
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            return PatchAttempt(False, proc.stderr.strip()[:800], diff)
        tmp.unlink(missing_ok=True)
        return PatchAttempt(True, "", diff)

    def run(self, ctx) -> SkillResult:
        commands: list[dict] = []
        claims: list[str] = []
        failure = ""
        for attempt in range(self.max_attempts):
            response = self.backend.complete(self._prompt(ctx, failure))
            raw_sha = ctx.blobs.put(response)
            diff = extract_diff(response)
            if diff is None:
                failure = "response contained no unified diff"
                claims.append(f"attempt {attempt + 1}: {failure}")
                commands.append({"cmd": "llm.author_patch", "exit": 1,
                                 "out": raw_sha})
                continue
            checked = self._validate(ctx, diff)
            commands.append({
                "cmd": f"git apply --check ({self.slot}, attempt {attempt + 1})",
                "exit": 0 if checked.ok else 1,
                "out": ctx.blobs.put(diff),
            })
            if not checked.ok:
                failure = checked.reason
                claims.append(f"attempt {attempt + 1}: patch rejected "
                              f"by git apply --check")
                continue
            target = ctx.queue_dir / f"{self.slot}.patch"
            target.write_text(diff, encoding="utf-8", newline="\n")
            claims.append(f"authored a patch for slot {self.slot}")
            return SkillResult(
                ok=True,
                claimed_props=frozenset({f"patch.queued:{self.slot}"}),
                claims=claims, commands=commands,
                artifacts={"diff": ctx.blobs.put(diff), "raw_response": raw_sha},
            )
        return SkillResult(ok=False, claims=claims, commands=commands)


def build_authoring_catalog(slots: list[str], suite: str = "unit") -> Catalog:
    """Catalog for episodes where LEDGER writes its own patches.

    Per slot: AuthorPatch (LLM, stochastic -- the model may fail to produce
    an appliable diff) then ApplyPatch (deterministic given a queued patch).
    Commit requires a passing suite and at least one applied patch.
    """
    if not slots:
        raise ValueError("at least one patch slot is required")
    fails = tuple(p.replace("unit", suite) for p in TEST_FAIL_PROPS)
    pass_prop = f"tests.pass:{suite}"
    actions = [
        Action("RunSuite", prec=frozenset(),
               add=frozenset({pass_prop}), delete=frozenset(fails),
               risk="exec", deterministic=False),
        Action("Commit",
               prec=frozenset({pass_prop, "patch.applied:" + slots[0]}),
               add=frozenset({"repo.clean"}), risk="write"),
    ]
    for slot in slots:
        actions.append(Action(
            # Preconditions are conjunctive (paper SS3.1), so "author only
            # when a suite is failing" is not expressible -- the two failure
            # buckets are alternatives, and requiring both is unsatisfiable.
            # An empty precondition is also the honest contract: authoring a
            # patch is always *possible*. Ordering is left to search, which
            # prefers RunSuite first because it is the shorter path to the
            # goal, and only reaches for AuthorPatch once that path is
            # falsified by reality.
            f"AuthorPatch:{slot}",
            prec=frozenset(),
            add=frozenset({f"patch.queued:{slot}"}),
            risk="write", deterministic=False))
        actions.append(Action(
            f"ApplyPatch:{slot}",
            prec=frozenset({f"patch.queued:{slot}"}),
            add=frozenset({f"patch.applied:{slot}"}),
            delete=frozenset({f"patch.queued:{slot}", "repo.clean",
                              pass_prop, *fails}),
            risk="write"))
    return Catalog(actions)
