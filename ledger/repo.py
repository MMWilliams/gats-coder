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


def fast_forward(repo_root: Path | str, branch: str) -> tuple[bool, str]:
    """Bring an episode branch into the repository's checked-out branch,
    only when that is a pure fast-forward.

    An episode works in its own worktree and commits to `ledger/<name>`;
    nothing it built is visible from the repository root until something
    moves it there. Fast-forward is the only integration that adds no
    commit of its own and cannot silently combine two histories: the
    branch was cut from the repository's HEAD, so if HEAD has not moved
    since, the merge is exactly the episode's verified commits and nothing
    else. Anything less clean is reported, not attempted.
    """
    root = Path(repo_root)
    current = git(root, "rev-parse", "--abbrev-ref", "HEAD",
                  check=False).stdout.strip()
    if not current or current == "HEAD":
        return False, "the repository is not on a branch (detached HEAD)"
    if current == branch:
        return True, f"{branch} is already checked out here"
    proc = git(root, "merge", "--ff-only", branch, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, (detail[-1] if detail else "git merge --ff-only failed")
    return True, f"fast-forwarded {current} to {branch}"


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
        # Episode directories get deleted -- by people tidying up, by a
        # failed run, by rm -rf. Git still has the worktree registered, and
        # a plain `add` then dies with "missing but already registered".
        # Prune first, and force past any leftover registration: a stale
        # entry is bookkeeping, not data.
        git(repo_root, "worktree", "prune", check=False)
        proc = git(repo_root, "worktree", "add", "--detach", str(dest),
                   "HEAD", check=False)
        if proc.returncode != 0:
            proc = git(repo_root, "worktree", "add", "-f", "--detach",
                       str(dest), "HEAD", check=False)
            if proc.returncode != 0:
                raise GitError("could not create the episode worktree: "
                               + proc.stderr.strip())
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

    def commits_behind(self) -> int:
        """How many commits the repository's HEAD is ahead of this worktree.

        A resumed episode keeps the worktree it was created with. If the
        spec moved on since -- new tests, a tightened contract -- the
        episode would verify against the OLD one and report success for
        work that no longer satisfies anybody. Silence there is worse than
        failure, so the caller is told.
        """
        proc = git(self.repo_root, "rev-list", "--count",
                   "HEAD", f"^{self.head()}", check=False)
        try:
            return int(proc.stdout.strip() or 0)
        except ValueError:
            return 0

    def head(self) -> str:
        return git(self.path, "rev-parse", "HEAD",
                   check=False).stdout.strip() or "HEAD"

    def remove(self) -> None:
        git(self.repo_root, "worktree", "remove", "--force", str(self.path))
