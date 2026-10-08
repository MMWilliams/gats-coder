"""Best-of-N by agreement, not by speed.

Agentless picks among passing patches by majority over AST-normalized diffs:
two attempts that reached the same code by different routes are stronger
evidence than one attempt that got there in fewer steps. Python files are
compared by `ast.dump` (formatting and comments vanish); everything else by
content with whitespace collapsed.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from pathlib import Path


def change_key(worktree: Path, base: str) -> str:
    """Hash of what the worktree changed relative to `base`, normalized."""
    worktree = Path(worktree)
    names = subprocess.run(["git", "diff", "--name-only", base],
                           cwd=worktree, capture_output=True, text=True,
                           timeout=60).stdout.split()
    h = hashlib.sha256()
    for rel in sorted(set(names)):
        f = worktree / rel
        text = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
        if rel.endswith(".py") and text:
            try:
                text = ast.dump(ast.parse(text))
            except SyntaxError:
                pass
        else:
            text = re.sub(r"\s+", " ", text).strip()
        h.update(f"{rel}\0{text}\0".encode("utf-8"))
    return h.hexdigest()


def rank(outcomes: list, base: str) -> list:
    """Successful attempts in the largest agreeing group first, then fewer
    steps, then name; failures after, as before."""
    keys = {}
    for o in outcomes:
        if o.success and o.journal_path:
            try:
                keys[o.name] = change_key(Path(o.journal_path).parent / "worktree", base)
            except (OSError, subprocess.SubprocessError):
                pass
    votes: dict[str, int] = {}
    for k in keys.values():
        votes[k] = votes.get(k, 0) + 1
    return sorted(outcomes, key=lambda o: (
        not o.success, -votes.get(keys.get(o.name, ""), 0), o.steps, o.name))
