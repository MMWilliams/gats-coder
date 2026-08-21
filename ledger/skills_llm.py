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

Reply in ONE of these two formats and nothing else -- no prose, no
explanation outside the blocks. Do not modify test files unless the task
explicitly asks you to.

FORMAT A -- complete files (use this when creating a file or rewriting most
of one; it is the easier format to get right):

FILE: path/relative/to/repo/root.js
```
<the complete contents of the file, exactly as it should end up>
```

Repeat the FILE block for each file you change. Give the WHOLE file every
time, not an excerpt: whatever you write replaces the file.

FORMAT B -- a unified diff, for a small edit to a large file:

Standard `git apply` format with a/ and b/ prefixes and correct @@ hunk
headers.
"""


STOPWORDS = frozenset("""
a an and are as at be but by can do does for from in into is it its make not
of on or please should so that the this to update use using when where which
will with add change code error bug issue fix failing file files repo
repository test tests suite
""".split())


def _first_instruction_file(directory: Path) -> str:
    for name in ("AGENTS.md", "LEDGER.md", "CLAUDE.md"):
        f = directory / name
        if f.is_file():
            try:
                return f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return ""


def project_instructions(root: Path, max_chars: int = 8000,
                         home: Path | None = None) -> str:
    """Standing instructions, AGENTS.md-style and layered like Codex:
    global (`~/.ledger/AGENTS.md`) first, repo root second, so the closer
    file speaks last and wins on conflict. First of AGENTS.md / LEDGER.md /
    CLAUDE.md at each level."""
    layers = []
    home_dir = Path(home) if home is not None else Path.home()
    global_part = _first_instruction_file(home_dir / ".ledger")
    if global_part:
        layers.append(global_part)
    local_part = _first_instruction_file(Path(root))
    if local_part:
        layers.append(local_part)
    return "\n\n".join(layers)[:max_chars]


def select_context(root: Path, query: str, k: int = 4,
                   max_file_bytes: int = 200_000) -> list[str]:
    """Deterministic context selection: rank tracked files against the task
    text + failure evidence. No embeddings, no RNG -- a path named in the
    evidence dominates, then identifier tokens in filenames, then content
    occurrences. Ties break lexicographically, so the same repo state and
    query always select the same files.
    """
    proc = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                          text=True, timeout=60)
    files = sorted(f for f in proc.stdout.split() if f)
    tokens = sorted({t for t in re.findall(r"[a-z_][a-z0-9_]{2,}",
                                           query.lower())
                     if t not in STOPWORDS})
    scores: dict[str, int] = {}
    for rel in files:
        f = root / rel
        try:
            if not f.is_file() or f.stat().st_size > max_file_bytes:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score = 0
        if rel in query:
            score += 50
        elif f.name in query:
            score += 30
        low = text.lower()
        for t in tokens:
            if t in rel.lower():
                score += 5
            score += min(low.count(t), 10)
        if score > 0:
            scores[rel] = score
    ranked = sorted(scores, key=lambda r: (-scores[r], r))
    return ranked[:k]


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



_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$")


def repair_hunk_counts(diff: str) -> str:
    """Recompute @@ hunk line counts from the hunk body.

    Models routinely miscount them -- a 273-line file announced as 342 --
    and git then reads past the end and calls the whole patch corrupt even
    though every line of content is correct. The counts are a pure function
    of the body, so recomputing them trusts nothing: the result still has
    to survive `git apply --check` exactly as before.
    """
    lines = diff.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _HUNK.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        old_start, new_start, tail = m.group(1), m.group(2), m.group(3)
        body, j = [], i + 1
        while j < len(lines):
            ln = lines[j]
            if ln.startswith("@@") or ln.startswith("diff --git"):
                break
            body.append(ln)
            j += 1
        while body and body[-1].strip() == "":
            body.pop()                       # trailing blank is not content
        old = sum(1 for ln in body if ln[:1] in (" ", "-"))
        new = sum(1 for ln in body if ln[:1] in (" ", "+"))
        out.append(f"@@ -{old_start},{old} +{new_start},{new} @@{tail}")
        out.extend(body)
        i = j
    return "\n".join(out) + "\n"


_FILE_BLOCK = re.compile(
    r"^FILE:[ \t]*(?P<path>[^\n`]+?)[ \t]*\n+```[^\n]*\n(?P<body>.*?)```",
    re.MULTILINE | re.DOTALL)


def extract_files(text: str) -> list[tuple[str, str]]:
    """Parse `FILE: path` + fenced-content blocks into (path, content).

    Writing a correct unified diff is a second skill on top of writing
    correct code -- hunk arithmetic, exact context lines -- and it is where
    smaller models fail while their actual code is fine. Letting them hand
    back whole files moves that burden to us, where it is deterministic.
    """
    out: list[tuple[str, str]] = []
    for m in _FILE_BLOCK.finditer(text):
        raw = m.group("path").strip().strip("`").replace("\\", "/")
        # Reject BEFORE normalising. Stripping "./" first would quietly
        # turn "../../etc/passwd" into "etc/passwd" and hand an escape
        # attempt back as a clean relative path.
        if (not raw or raw.startswith("/") or raw.startswith("~")
                or ".." in raw.split("/")
                or re.match(r"^[A-Za-z]:", raw)):
            continue                      # never write outside the worktree
        path = raw
        while path.startswith("./"):
            path = path[2:]
        if not path:
            continue
        body = m.group("body")
        if not body.endswith("\n"):
            body += "\n"
        out.append((path, body))
    return out


def diff_from_files(root: Path, files: list[tuple[str, str]]) -> str:
    """Build a unified diff from whole-file contents, deterministically.

    The result still has to pass `git apply --check`; this only removes the
    diff *format* from the model's job, not the verification.
    """
    import difflib

    chunks: list[str] = []
    for rel, new_text in files:
        target = Path(root) / rel
        if target.is_file():
            old_text = target.read_text(encoding="utf-8", errors="replace")
            old_lines = old_text.splitlines(keepends=True)
            from_file = f"a/{rel}"
        else:
            old_lines = []
            from_file = "/dev/null"
        new_lines = new_text.splitlines(keepends=True)
        if old_lines == new_lines:
            continue                                   # nothing to change
        body = list(difflib.unified_diff(
            old_lines, new_lines, fromfile=from_file, tofile=f"b/{rel}", n=3))
        if not body:
            continue
        header = f"diff --git a/{rel} b/{rel}\n"
        if from_file == "/dev/null":
            header += "new file mode 100644\n"
        text = "".join(body)
        if not text.endswith("\n"):
            text += "\n"
        chunks.append(header + text)
    return "".join(chunks)


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
                 max_attempts: int = 2, meter=None,
                 product_kind: str = "", product_require=None):
        self.slot = slot
        self.backend = backend
        self.task_text = task_text
        self.context_files = list(context_files or [])
        self.max_attempts = max_attempts
        self.meter = meter
        self.product_kind = product_kind
        self.product_require = list(product_require or []) or None

    def _prompt(self, ctx, failure: str = "") -> str:
        extra_parts = []
        # What makes this KIND of software usable, in front of the model
        # while it writes. Far cheaper than discovering afterwards that the
        # steering input was stored on the car and read by nothing.
        if self.product_kind:
            from .product import prompt_block
            block = prompt_block(self.product_kind, self.product_require)
            if block:
                extra_parts.append(block)
        instructions = project_instructions(ctx.worktree)
        if instructions:
            extra_parts.append("PROJECT INSTRUCTIONS (from the repository)\n"
                               + instructions)
        evidence = getattr(ctx, "suite_evidence", {}) or {}
        evidence_tail = "\n".join(e["tail"] for _, e in sorted(evidence.items()))
        if evidence_tail:
            extra_parts.append("MOST RECENT FAILING TEST OUTPUT\n"
                               + evidence_tail)
        files = self.context_files or select_context(
            ctx.worktree, self.task_text + "\n" + evidence_tail)
        if files:
            extra_parts.append("RELEVANT FILE CONTENTS\n"
                               + read_files(ctx.worktree, files))
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
            if self.meter is not None:
                if self.meter.exhausted:
                    claims.append("author budget exhausted "
                                  f"({self.meter.spent}/{self.meter.limit})")
                    break
                self.meter.charge()
            ctx.say(f"authoring a patch for slot {self.slot}"
                    + (f" (attempt {attempt + 1})" if attempt else ""))
            try:
                response = self.backend.complete(self._prompt(ctx, failure))
            except Exception as exc:
                # A backend failure (timeout, network, provider error) is a
                # non-effect, never a crash: the planner re-plans and the
                # budget meter escalates persistent failure (SS12.3).
                reason = f"backend error: {exc}"
                claims.append(f"attempt {attempt + 1}: {reason}")
                commands.append({"cmd": "llm.author_patch", "exit": -1,
                                 "out": ctx.blobs.put(reason)})
                failure = ""
                continue
            raw_sha = ctx.blobs.put(response)
            # Whole files first: it is the format models get right, and the
            # diff we build from them is correct by construction.
            written = extract_files(response)
            diff = None
            if written:
                diff = diff_from_files(ctx.worktree, written) or None
                if diff:
                    claims.append(
                        "built the patch from "
                        + ", ".join(rel for rel, _ in written))
            if diff is None:
                diff = extract_diff(response)
            if diff is None:
                failure = ("response contained neither FILE: blocks nor a "
                           "unified diff")
                claims.append(f"attempt {attempt + 1}: {failure}")
                commands.append({"cmd": "llm.author_patch", "exit": 1,
                                 "out": raw_sha})
                continue
            checked = self._validate(ctx, diff)
            if not checked.ok:
                # The content may be sound and only the hunk arithmetic
                # wrong; recompute it and let git judge again.
                repaired = repair_hunk_counts(diff)
                if repaired != diff:
                    retry = self._validate(ctx, repaired)
                    if retry.ok:
                        diff, checked = repaired, retry
                        claims.append("repaired the patch's hunk counts")
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
            # Authoring is otherwise always *possible*, so the only honest
            # precondition is the resource one: LLM budget remains (SS12.3).
            # When the meter runs dry the proposition vanishes and authoring
            # becomes symbolically inapplicable -- the budget surfaces as
            # planner behavior, not an exception. Ordering is left to
            # search, which prefers RunSuite first because it is the
            # shorter path to the goal, and only reaches for AuthorPatch
            # once that path is falsified by reality.
            f"AuthorPatch:{slot}",
            prec=frozenset({"budget.llm:ok"}),
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
