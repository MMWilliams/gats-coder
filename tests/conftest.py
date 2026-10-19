"""Shared fixtures: a real small repo with a committed bug + failing test,
and a queued fix patch."""

import difflib
import shutil
import subprocess
import sys

import pytest

needs_git = pytest.mark.skipif(shutil.which("git") is None,
                               reason="git not available")

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
GOAL = frozenset({"tests.pass:unit", "repo.clean"})
TEST_CMD = [sys.executable, "-m", "pytest", "-q", "--no-header",
            "-p", "no:cacheprovider"]


@pytest.fixture(autouse=True)
def _isolated_vault(monkeypatch):
    """Keep tests out of the real keychain (DPAPI keeps nothing outside the
    file, so it is safe), and start each test knowing no secrets."""
    from ledger import credentials
    if sys.platform != "win32":
        monkeypatch.setenv("LEDGER_VAULT", "file")
    credentials._LOADED.clear()
    yield
    credentials._LOADED.clear()


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                   check=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "calc.py").write_text(BUGGY, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "buggy initial commit")
    return root


def queue_fix(episode_dir):
    queue = episode_dir / "queue"
    queue.mkdir(parents=True)
    diff = difflib.unified_diff(
        BUGGY.splitlines(keepends=True), FIXED.splitlines(keepends=True),
        fromfile="a/calc.py", tofile="b/calc.py",
    )
    (queue / "fix-001.patch").write_text("".join(diff), encoding="utf-8")
