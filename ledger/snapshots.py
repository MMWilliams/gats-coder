"""A tree hash of the worktree before every action that changes it.

A separate git directory per episode, so nothing touches the user's index,
refs or history (opencode's snapshot/index.ts and gemini-cli's shadow repo
work the same way). Its object store borrows the repository's through
`objects/info/alternates`, so tracked content is never stored twice.

The hash goes into the EXEC entry, which puts it inside the hash chain: the
journal can then say not only what each action claimed, but exactly which
tree it started from -- and `ledger rewind` can put that tree back.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class Snapshots:
    def __init__(self, git_dir: Path, worktree: Path):
        self.git_dir = Path(git_dir)
        self.worktree = Path(worktree)

    def _git(self, *args: str) -> str:
        env = {**os.environ, "GIT_DIR": str(self.git_dir),
               "GIT_WORK_TREE": str(self.worktree)}
        env.pop("GIT_INDEX_FILE", None)
        proc = subprocess.run(["git", *args], cwd=self.worktree, env=env,
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"snapshot git {args[0]} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def _init(self) -> None:
        if (self.git_dir / "HEAD").exists():
            return
        subprocess.run(["git", "init", "--bare", "-q", str(self.git_dir)],
                       capture_output=True, check=True, timeout=60)
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=self.worktree, capture_output=True, text=True, timeout=60)
        if common.returncode == 0 and common.stdout.strip():
            alt = self.git_dir / "objects" / "info" / "alternates"
            alt.write_text(str(Path(common.stdout.strip()) / "objects") + "\n",
                           encoding="utf-8")

    def take(self) -> str:
        self._init()
        self._git("add", "-A", "--", ".")
        return self._git("write-tree")

    def restore(self, tree: str) -> str:
        """Put `tree` back in the worktree. Returns the tree it replaced, taken
        first, so a rewind can itself be rewound."""
        backup = self.take()
        added = self._git("diff", "--name-only", "--diff-filter=A", tree, backup)
        for rel in added.splitlines():
            (self.worktree / rel).unlink(missing_ok=True)
        self._git("read-tree", tree)
        self._git("checkout-index", "-a", "-f")
        return backup
