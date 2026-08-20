"""Wave-3 parity: episode resume, best-of-N attempts, CreatePR via a stub
gh, and policy rules inside a real episode."""

import json
import os
import subprocess
import sys

from conftest import GOAL, TEST_CMD, needs_git, queue_fix

import ledger.cli as cli
from ledger.executor import PolicyGate, run_repo_episode
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.repo import Worktree, git

FIX_DIFF = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n-    return a - b\n+    return a + b\n")


def _responder(prompt: str) -> str:
    if "REPOSITORY FILES" in prompt:
        return FIX_DIFF
    return json.dumps({"goal": ["tests.pass:unit"], "rationale": "green"})


# -- resume ------------------------------------------------------------------

@needs_git
def test_worktree_reuse_preserves_state(repo, tmp_path):
    wt = Worktree.add(repo, tmp_path / "wt", branch="ledger/keep")
    (wt.path / "scratch.txt").write_text("progress", encoding="utf-8")
    again = Worktree.reuse(repo, tmp_path / "wt", branch="ledger/keep")
    assert again.path == wt.path and again.branch == "ledger/keep"
    assert (again.path / "scratch.txt").read_text(encoding="utf-8") == "progress"


@needs_git
def test_resume_after_budget_exhaustion(repo, tmp_path):
    """Fail on budget with a useless model, resume the same episode with a
    working one: the second segment reuses the worktree and warm graph and
    reaches the goal."""
    episode_dir = tmp_path / "comeback"
    goal = frozenset({"tests.pass:unit"})
    r1, _, j1 = run_repo_episode(
        repo, episode_dir, goal, TEST_CMD,
        backend=FakeBackend("I cannot write diffs."), slots=["a"],
        max_author_attempts=1)
    assert not r1.success
    assert j1.name == "episode.jsonl"

    r2, executor2, j2 = run_repo_episode(
        repo, episode_dir, goal, TEST_CMD,
        backend=FakeBackend(_responder), slots=["a"], resume=True)
    assert r2.success, r2.plan
    assert j2.name == "episode.r2.jsonl"
    header = Journal.read(str(j2))[0]
    assert header["resumed"] is True and header["segment"] == 2
    ok, err = Journal.verify_chain(str(j2))
    assert ok, err
    # Both segments verify independently; the first is untouched.
    ok, err = Journal.verify_chain(str(j1))
    assert ok, err


# -- policy rules in an episode ---------------------------------------------

@needs_git
def test_policy_deny_beats_permissive_profile(repo, tmp_path):
    """autonomous gates nothing by profile, but a deny rule still blocks the
    family -- policy outranks profile."""
    episode_dir = tmp_path / "denied-commit"
    queue_fix(episode_dir)
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD,
        gate=PolicyGate("autonomous", rules={"deny": ["Commit"]}),
        profile="autonomous")
    assert not result.success
    assert executor.denials > 0
    entries = Journal.read(str(journal_path))
    denied = [e for e in entries if e["t"] == "GATE" and not e["approved"]]
    assert denied and all(e["action"] == "Commit" for e in denied)


# -- best-of-N attempts ------------------------------------------------------

@needs_git
def test_run_attempts_picks_a_winner(repo, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_backend",
                        lambda args, cfg=None: FakeBackend(_responder))
    d = repo / ".ledger"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"test_cmd": TEST_CMD}),
                                   encoding="utf-8")
    rc = cli.main(["run", "fix the failing test", "--repo", str(repo),
                   "--attempts", "3", "--yes", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert len(out["attempts"]) == 3
    assert out["winner"] is not None
    assert out["winner_journal"].endswith("episode.jsonl")
    assert all(a["success"] for a in out["attempts"])


def test_run_attempts_requires_yes(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_backend", lambda args, cfg=None: FakeBackend("x"))
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   capture_output=True)
    rc = cli.main(["run", "task", "--repo", str(repo), "--attempts", "2"])
    assert rc == 1
    assert "--yes" in capsys.readouterr().err


# -- CreatePR via stub gh ----------------------------------------------------

def _stub_gh(tmp_path, monkeypatch):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    marker = tmp_path / "pr-created.marker"
    stub_py = stub_dir / "gh_stub.py"
    stub_py.write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "marker = Path(os.environ['LEDGER_TEST_PR_MARKER'])\n"
        "a = sys.argv[1:3]\n"
        "if a == ['pr', 'create']:\n"
        "    marker.write_text('created', encoding='utf-8')\n"
        "    print('https://example.invalid/pr/1')\n"
        "    sys.exit(0)\n"
        "if a == ['pr', 'view']:\n"
        "    sys.exit(0 if marker.exists() else 1)\n"
        "sys.exit(1)\n", encoding="utf-8")
    (stub_dir / "gh.bat").write_text(
        f'@echo off\n"{sys.executable}" "{stub_py}" %*\n', encoding="utf-8")
    (stub_dir / "gh").write_text(  # POSIX fallback
        f'#!/bin/sh\nexec "{sys.executable}" "{stub_py}" "$@"\n',
        encoding="utf-8")
    monkeypatch.setenv("LEDGER_TEST_PR_MARKER", str(marker))
    monkeypatch.setenv("PATH",
                       str(stub_dir) + os.pathsep + os.environ["PATH"])
    return marker


@needs_git
def test_create_pr_flow(repo, tmp_path, monkeypatch):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                   capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo,
                   check=True, capture_output=True)
    marker = _stub_gh(tmp_path, monkeypatch)

    episode_dir = tmp_path / "prflow"
    queue_fix(episode_dir)
    goal = GOAL | {"repo.pushed:ledger/prflow", "repo.pr:ledger/prflow"}
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, goal, TEST_CMD, pr=True)

    assert result.success, result.plan
    assert result.plan[-1] == "CreatePR"
    assert "PushBranch" in result.plan
    assert marker.exists()
    assert executor.incidents == 0
    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err



@needs_git
def test_worktree_survives_a_deleted_episode_directory(repo, tmp_path):
    """Regression: deleting an episode directory left the worktree
    registered in git, and the next run died with "missing but already
    registered" instead of recovering."""
    import shutil as _shutil
    dest = tmp_path / "wt"
    Worktree.add(repo, dest, branch="ledger/gone")
    _shutil.rmtree(dest)                       # the tidy-up that broke it

    again = Worktree.add(repo, dest, branch="ledger/gone")
    assert again.path.is_dir()
    assert (again.path / ".git").exists()


@needs_git
def test_episode_scratch_is_never_committable(repo, tmp_path):
    """An episode writes worktrees and blobs into .ledger/. If that is not
    ignored, one `git add -A` sweeps an embedded git repo into the user's
    history. `ledger init` wrote the ignore file; an episode started
    without init must too."""
    assert not (repo / ".ledger" / ".gitignore").exists()
    queue_fix(tmp_path / "ep")
    run_repo_episode(repo, tmp_path / "ep", GOAL, TEST_CMD)

    ignore = repo / ".ledger" / ".gitignore"
    assert ignore.is_file()
    body = ignore.read_text(encoding="utf-8")
    assert "episodes/" in body and "sessions/" in body


@needs_git
def test_rerunning_an_episode_name_reuses_its_worktree(repo, tmp_path):
    """Regression: re-running the same episode name died with "worktree
    already exists". An existing worktree is a fact about the filesystem --
    it must be re-entered, whether or not --resume was asked for."""
    episode_dir = tmp_path / "same-name"
    queue_fix(episode_dir)
    r1, _, _ = run_repo_episode(repo, episode_dir, GOAL, TEST_CMD)
    assert r1.success

    # no resume flag, same directory: must not raise
    r2, _, _ = run_repo_episode(repo, episode_dir, GOAL, TEST_CMD)
    assert (episode_dir / "worktree" / ".git").exists()
    assert r2 is not None
