"""What a failing suite says, reduced to something that can repeat.

Every open-source harness studied (HARNESS_STUDY.md) detects loops by hashing
the action. LEDGER's two unattended failures repeated the OUTCOME instead:
nine different patches, one identical ImportError. So the thing tracked here
is the failure, normalized until two runs of the same defect hash the same.

Also here: the deterministic evidence a stuck author is missing -- where a
name the failure mentions is defined, referenced and recently changed, and
which repo-local `from x import n` no longer resolves.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .sensors import Sensor

#: Consecutive identical failures before the author is told to change approach.
NUDGE_AT = 2
#: ...and before authoring is withdrawn and the episode escalates `stuck`.
STUCK_AT = 5
STUCK_PROP = "fail.stuck"
#: The author replied INFEASIBLE with an argument; authoring is withdrawn and
#: the episode escalates with that argument instead of spending the budget.
INFEASIBLE_PROP = "author.infeasible"

_EXC_LINE = re.compile(
    r"^(?:E\s+)?([A-Za-z_][\w.]*(?:Error|Exception|Exit|Failure))\b(?::\s*(.*))?$")
_SUMMARY = re.compile(r"^(FAILED|ERROR)\s+(\S+)")
_COLLECT = re.compile(r"errors? (?:while |during )?collect|ImportError while importing",
                      re.IGNORECASE)


def _norm(text: str) -> str:
    """Strip what differs between two runs of the same defect."""
    text = re.sub(r"0x[0-9a-fA-F]+", "0x?", text)
    # Directory prefixes (absolute or relative), keeping the basename.
    text = re.sub(r"(?:[A-Za-z]:)?(?:[\w.\-]*[\\/])+(?=[\w.\-])", "", text)
    text = re.sub(r"(?<=[\w)\]'\"]):\d+(?::\d+)?", "", text)
    text = re.sub(r"\bline \d+", "line ?", text)
    text = re.sub(r"\b\d+(?:\.\d+)?s\b", "?s", text)
    return re.sub(r"\s+", " ", text).strip()[:200]


@dataclass(frozen=True)
class Signature:
    sig: str        # 12 hex
    phase: str      # "collect" | "test"
    summary: str    # human-readable, first few items


def failure_signature(output: str) -> Signature:
    """Normalize a failing suite's output into a stable signature.

    Items are the pytest FAILED/ERROR ids plus every exception line, with
    line numbers, addresses, durations and directories removed (Agentless
    compares flake8 output the same way). Output with neither falls back to
    its last lines with every digit masked.
    """
    items: set[str] = set()
    for raw in output.splitlines():
        line = raw.strip()
        m = _SUMMARY.match(line)
        if m:
            items.add(f"{m.group(1)} {_norm(m.group(2))}")
            continue
        m = _EXC_LINE.match(line)
        if m:
            items.add(_norm(f"{m.group(1)}: {m.group(2) or ''}"))
    if not items:
        tail = [ln.strip() for ln in output.splitlines() if ln.strip()][-15:]
        items = {re.sub(r"\d", "#", _norm(ln)) for ln in tail}
    ordered = sorted(items)[:40]
    digest = hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest()[:12]
    phase = "collect" if _COLLECT.search(output) else "test"
    return Signature(digest, phase, "; ".join(ordered[:3]))


@dataclass
class FailureTracker:
    """Consecutive-identical-failure counter, fed by RunSuite, read by the
    sensor (escalation) and the author (a different approach)."""

    last: str = ""
    phase: str = ""
    summary: str = ""
    repeat: int = 0
    # Diffs applied since this signature first appeared: what did not work.
    attempts: list = field(default_factory=list)
    # The author's INFEASIBLE argument, verbatim; empty until given.
    infeasible: str = ""

    def observe(self, passed: bool, output: str) -> Signature | None:
        if passed:
            self.last, self.phase, self.summary = "", "", ""
            self.repeat, self.attempts = 0, []
            return None
        sig = failure_signature(output)
        if sig.sig == self.last:
            self.repeat += 1
        else:
            self.last, self.repeat, self.attempts = sig.sig, 1, []
        self.phase, self.summary = sig.phase, sig.summary
        return sig

    def applied(self, diff: str) -> None:
        self.attempts = (self.attempts + [diff[:1500]])[-3:]

    @property
    def stuck(self) -> bool:
        return self.repeat >= STUCK_AT

    def prompt_block(self) -> str:
        if self.repeat < NUDGE_AT:
            return ""
        tried = "\n\n".join(f"--- attempt {i + 1} ---\n{d}"
                            for i, d in enumerate(self.attempts))
        return (f"THE SAME FAILURE HAS NOW OCCURRED {self.repeat} TIMES IN A ROW "
                f"(signature {self.last}, {self.phase} phase): {self.summary}\n"
                "The patches below were applied and did not change it. Do not "
                "repeat them. Re-read the failing line and the definitions it "
                "names (see WHERE THE FAILING NAMES APPEAR), consider that the "
                "defect is in a different file from the one you have been "
                "editing, and take a different approach."
                + (f"\n\n{tried}" if tried else ""))

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.__dict__, sort_keys=True),
                              encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "FailureTracker":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return FailureTracker()
        return FailureTracker(**{k: data[k] for k in
                                 ("last", "phase", "summary", "repeat", "attempts",
                                  "infeasible")
                                 if k in data})


def failure_sensor(tracker: FailureTracker) -> Sensor:
    """Emits `fail.stuck` once one failure has survived STUCK_AT runs, and
    `author.infeasible` once the author has argued the task cannot be done.
    AuthorPatch forbids both, so a loop that verification cannot break stops
    spending budget and escalates instead."""

    def probe(root: Path) -> set:
        props = set()
        if tracker.stuck:
            props.add(STUCK_PROP)
        if tracker.infeasible:
            props.add(INFEASIBLE_PROP)
        return props

    return Sensor(name="failure_signature", version="1",
                  cost_tier="instant", probe=probe)


# ---------------------------------------------------------------------------
# Symbol evidence
# ---------------------------------------------------------------------------

_NAME_PATTERNS = (
    re.compile(r"cannot import name '(\w+)'"),
    re.compile(r"name '(\w+)' is not defined"),
    re.compile(r"has no attribute '(\w+)'"),
    re.compile(r"No module named '([\w.]+)'"),
)


def failing_names(output: str, limit: int = 3) -> list[str]:
    names: list[str] = []
    for pattern in _NAME_PATTERNS:
        for m in pattern.finditer(output):
            name = m.group(1).rsplit(".", 1)[-1]
            if name not in names:
                names.append(name)
    return names[:limit]


def _run(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout if proc.returncode in (0, 1) else ""


def symbol_evidence(root: Path, output: str,
                    max_hits: int = 15) -> tuple[str, list[str]]:
    """(prompt block, paths) for the names a failure mentions.

    `git grep -w` finds every line naming them -- the one search that
    reliably catches an import left behind by a rename -- and the diff and
    `git log -S` lines mentioning them show the rename itself.
    """
    root = Path(root)
    blocks: list[str] = []
    paths: list[str] = []
    for name in failing_names(output):
        hits = _run(root, "grep", "-n", "-w", "-I", "--untracked", "-e", name)
        lines = hits.splitlines()[:max_hits]
        for ln in lines:
            rel = ln.split(":", 1)[0]
            if rel not in paths:
                paths.append(rel)
        changed = [ln for ln in _run(root, "diff", "-U0", "HEAD").splitlines()
                   if ln.startswith(("+++", "---")) or
                   (ln[:1] in "+-" and re.search(rf"\b{re.escape(name)}\b", ln))]
        # Keep a file header only when a matching line follows it.
        kept = [ln for i, ln in enumerate(changed)
                if not ln.startswith(("+++", "---"))
                or (i + 1 < len(changed)
                    and not changed[i + 1].startswith(("+++", "---")))]
        part = f"`{name}`:\n" + ("\n".join(lines) if lines else "(no occurrences)")
        if kept:
            part += "\nuncommitted changes mentioning it:\n" + "\n".join(kept[:20])
        history = _run(root, "log", "-n", "3", f"-S{name}", "--format=%h %s",
                       "--name-only").strip()
        if history:
            part += "\ncommits that added or removed it:\n" + history
        blocks.append(part)
    if not blocks:
        return "", []
    return "WHERE THE FAILING NAMES APPEAR\n" + "\n\n".join(blocks), paths


# ---------------------------------------------------------------------------
# Dangling repo-local imports
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrokenImport:
    importer: str   # repo-relative path
    line: int
    module: str     # dotted
    name: str

    def __str__(self) -> str:
        return (f"{self.importer}:{self.line} imports `{self.name}` from "
                f"`{self.module}`, which does not define it")


def _module_index(files: list[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        index.setdefault(".".join(parts), rel)
        if parts[0] == "src" and len(parts) > 1:
            index.setdefault(".".join(parts[1:]), rel)
    return index


def _defined(tree: ast.Module) -> set[str] | None:
    """Top-level names a module defines; None when that is unknowable
    (star import or a module-level __getattr__)."""
    names: set[str] = set()
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "__getattr__":
                return None
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            for attr in ("body", "orelse", "finalbody", "handlers"):
                stack.extend(getattr(node, attr, []) or [])
        elif isinstance(node, ast.ExceptHandler):
            stack.extend(node.body)
    return names


def tracked_python(root: Path) -> list[str]:
    out = _run(root, "ls-files", "-c", "-o", "--exclude-standard", "*.py")
    return sorted(set(out.split()))


def broken_imports(root: Path, scope: list[str] | None = None,
                   files: list[str] | None = None) -> list[BrokenImport]:
    """Repo-local `from x import n` where x exists and does not define n.

    Imports of modules outside the repository are not judged: only what the
    repository itself can answer. `scope` limits which importers are read.
    """
    root = Path(root)
    files = files if files is not None else tracked_python(root)
    index = _module_index(files)
    trees: dict[str, ast.Module | None] = {}

    def tree_of(rel: str):
        if rel not in trees:
            try:
                trees[rel] = ast.parse((root / rel).read_text(
                    encoding="utf-8", errors="replace"), filename=rel)
            except (OSError, SyntaxError, ValueError):
                trees[rel] = None
        return trees[rel]

    found: list[BrokenImport] = []
    for rel in sorted(scope if scope is not None else files):
        if not rel.endswith(".py"):
            continue
        tree = tree_of(rel)
        if tree is None:
            continue
        parts = rel[:-3].split("/")
        package = parts[:-1]       # for __init__.py too: the package itself
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                base = package[:len(package) - (node.level - 1)] \
                    if node.level - 1 <= len(package) else None
                if base is None:
                    continue
                module = ".".join(base + ([node.module] if node.module else []))
                if module.startswith("src.") and module not in index:
                    module = module[4:]
            else:
                module = node.module or ""
            target = index.get(module)
            if target is None:
                continue
            defined = None
            target_tree = tree_of(target)
            if target_tree is not None:
                defined = _defined(target_tree)
            if defined is None:
                continue
            for alias in node.names:
                if alias.name == "*" or alias.name in defined:
                    continue
                if f"{module}.{alias.name}" in index:
                    continue                      # a submodule, not a name
                found.append(BrokenImport(rel, node.lineno, module, alias.name))
    return found


def importers_of(root: Path, modules: list[str], files: list[str]) -> list[str]:
    """Cheap pre-filter: files whose text mentions one of the modules' last
    names. Over-inclusive by design; broken_imports decides."""
    tails = {m.rsplit(".", 1)[-1] for m in modules if m}
    hits = []
    for rel in files:
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "import" in text and any(t in text for t in tails):
            hits.append(rel)
    return hits
