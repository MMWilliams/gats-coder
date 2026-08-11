"""Git worktree sandbox (design SS8.3) and content fingerprinting (SS4.2).

Episodes run in a worktree on their own branch; the primary working copy is
never touched. The fingerprint is a pure function of working-tree *content*
(tracked + untracked-unignored files), deliberately independent of HEAD, so
expensive sensor results (test status) survive a commit that doesn't change
file content.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(root: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=120
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def content_fingerprint(root: Path | str) -> str:
    """SHA-256 over sorted (path, file-content-hash) of tracked and
    untracked-unignored files. Same content => same fingerprint."""
    root = Path(root)
    tracked = git(root, "ls-files").stdout.splitlines()
    untracked = git(root, "ls-files", "-o", "--exclude-standard").stdout.splitlines()
    h = hashlib.sha256()
    for rel in sorted(set(tracked) | set(untracked)):
        f = root / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        if f.is_file():
            h.update(hashlib.sha256(f.read_bytes()).digest())
        else:
            h.update(b"<absent>")
        h.update(b"\0")
    return h.hexdigest()


class Worktree:
    def __init__(self, repo_root: Path, path: Path, branch: str):
        self.repo_root = repo_root
        self.path = path
        self.branch = branch

    @classmethod
    def add(cls, repo_root: Path | str, dest: Path | str,
            branch: str) -> "Worktree":
        repo_root, dest = Path(repo_root), Path(dest)
        git(repo_root, "worktree", "add", "--detach", str(dest), "HEAD")
        # -B: episode branches are disposable; re-running a demo reuses the name.
        git(dest, "checkout", "-B", branch)
        return cls(repo_root, dest, branch)

    @classmethod
    def reuse(cls, repo_root: Path | str, dest: Path | str,
              branch: str) -> "Worktree":
        """Re-enter an existing episode worktree (resume): applied patches,
        commits, and untracked state are all still there. Only re-attaches;
        never resets."""
        repo_root, dest = Path(repo_root), Path(dest)
        if not (dest / ".git").exists():
            raise GitError(f"{dest} is not an existing worktree")
        current = git(dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if current != branch:
            git(dest, "checkout", "-B", branch)
        return cls(repo_root, dest, branch)

    def remove(self) -> None:
        git(self.repo_root, "worktree", "remove", "--force", str(self.path))
