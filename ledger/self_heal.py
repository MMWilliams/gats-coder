"""Bounded repair of Ledger framework crashes during a long-running loop.

Task-test failures remain in the task loop. Only an unhandled Ledger
traceback reaches this module. The repair candidate works on a snapshot of
the current source (including uncommitted changes), never on the live tree.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import traceback


MAX_REPAIRS = 2


def resource_exhaustion(details: str) -> bool:
    """A starved host needs a bounded retry, not a source-code mutation."""
    return any(marker in details for marker in (
        "[WinError 1455]", "paging file is too small",
        "MemoryError", "Cannot allocate memory"))


def framework_error(details: str) -> bool:
    """Do not treat a Blender/Godot/test traceback as a Ledger bug."""
    return bool(re.search(r'File "[^"]+[\\/]ledger[\\/][^\"]+\.py"', details))


def allowed_paths(paths: list[str]) -> bool:
    """A repair may change implementation, never tests or acceptance rules."""
    return bool(paths) and all(
        path.startswith("ledger/") and path.endswith(".py")
        and ".." not in Path(path).parts for path in paths)


def _run(argv: list[str], cwd: Path, *, timeout: int = 120,
         input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, input=input_bytes,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)


def _snapshot(source: Path, target: Path) -> None:
    files = _run(["git", "ls-files", "--cached", "--others",
                  "--exclude-standard", "-z"], source)
    if files.returncode:
        raise RuntimeError("could not enumerate Ledger source for repair: "
                           + files.stdout.decode(errors="replace")[-500:])
    for raw in files.stdout.split(b"\0"):
        if not raw:
            continue
        rel = Path(os.fsdecode(raw))
        original = source / rel
        if original.is_file() and not original.is_symlink():
            destination = target / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
    for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                 ["git", "-c", "user.name=ledger",
                  "-c", "user.email=ledger@local", "commit", "-qm", "snapshot"]):
        result = _run(argv, target)
        if result.returncode:
            raise RuntimeError("could not create isolated repair snapshot: "
                               + result.stdout.decode(errors="replace")[-500:])


def try_repair(details: str, *, source: Path, episode_dir: Path,
               depth: int = 0, backend_options: list[str] | None = None) -> bool:
    """Ask Ledger to repair itself, verify, then apply only the passing patch."""
    if depth >= MAX_REPAIRS or not framework_error(details):
        return False
    episode_dir.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(details.encode("utf-8", errors="replace")).hexdigest()[:12]
    record = {"signature": signature, "depth": depth, "result": "started"}
    log = episode_dir / "self_heal.jsonl"

    def write_record() -> None:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    try:
        with tempfile.TemporaryDirectory(prefix="ledger-self-heal-") as name:
            snapshot = Path(name)
            _snapshot(source, snapshot)
            baseline = _run(["git", "rev-parse", "HEAD"], snapshot).stdout.strip().decode()
            task = (
                "Repair this unhandled Ledger framework exception in the "
                "Ledger Python implementation. Modify only ledger/*.py; do "
                "not edit tests, verification rules, or the game project. "
                "Find the root cause, make a minimal fix, and run the full "
                "existing test suite. The game loop must be able to resume.\n\n"
                "TRACEBACK\n" + details[-8000:])
            command = [sys.executable, "-m", "ledger", "run", task,
                       "--repo", str(snapshot), "--name", f"self-heal-{signature}",
                       "--kind", "library", "--no-product-review",
                       "--slots", "fix", "--require-patch-slot", "fix",
                       "--test", "python -m pytest -q", "--yes",
                       "--no-research", "--author-timeout", "900"]
            command.extend(backend_options or [])
            repair = _run(command, source, timeout=3600)
            if repair.returncode:
                record["result"] = "candidate_failed"
                record["detail"] = repair.stdout.decode(errors="replace")[-1200:]
                return False
            worktree = snapshot / ".ledger" / "episodes" / f"self-heal-{signature}" / "worktree"
            changed = _run(["git", "diff", "--name-only", baseline, "HEAD"], worktree)
            paths = changed.stdout.decode(errors="replace").splitlines()
            if changed.returncode or not allowed_paths(paths):
                record["result"] = "protected_path_rejected"
                record["paths"] = paths
                return False
            verified = _run([sys.executable, "-m", "pytest", "-q"],
                            worktree, timeout=1200)
            if verified.returncode:
                record["result"] = "candidate_tests_failed"
                record["detail"] = verified.stdout.decode(errors="replace")[-1200:]
                return False
            patch = _run(["git", "diff", "--binary", baseline, "HEAD"],
                         worktree).stdout
            if _run(["git", "apply", "--check", "-"], source,
                    input_bytes=patch).returncode:
                record["result"] = "live_patch_conflict"
                return False
            applied = _run(["git", "apply", "-"], source, input_bytes=patch)
            if applied.returncode:
                record["result"] = "live_patch_failed"
                return False
            final = _run([sys.executable, "-m", "pytest", "-q"],
                         source, timeout=1200)
            if final.returncode:
                _run(["git", "apply", "-R", "-"], source, input_bytes=patch)
                record["result"] = "live_tests_failed_rolled_back"
                record["detail"] = final.stdout.decode(errors="replace")[-1200:]
                return False
            record["result"] = "repaired"
            record["paths"] = paths
            return True
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        record["result"] = "repair_error"
        record["detail"] = str(exc)[-1200:]
        return False
    finally:
        write_record()


def current_traceback() -> str:
    return traceback.format_exc()
