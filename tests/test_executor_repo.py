"""M2 exit criterion: end-to-end episode on a real small repo (attended),
verify-chain green.

Scenario: a repo with a committed bug and a failing test; a queued patch
fixes it. The planner starts with L1's optimistic RunSuite contract, reality
diverges (tests fail), L2 learns this repo, and the episode recovers:
patch -> re-test -> commit. Every step is journaled and replayable.
"""

import json
import subprocess

from conftest import FIXED, GOAL, TEST_CMD, needs_git, queue_fix

from ledger.journal import Journal
from ledger.executor import BlobStore, resume_suite_evidence, run_repo_episode
from ledger.llm import FakeBackend
from ledger.replay import replay


def test_resume_keeps_latest_unaddressed_suite_failure(tmp_path):
    episode = tmp_path / "episode"
    sha = BlobStore(episode / "blobs").put("Blender traceback: line 423")
    (episode / "episode.jsonl").write_text(json.dumps({
        "t": "EXEC", "action": "RunSuite",
        "commands": [{"exit": 1, "out": sha}]}) + "\n", encoding="utf-8")
    assert "line 423" in resume_suite_evidence(episode)["unit"]["tail"]
    (episode / "episode.r2.jsonl").write_text(json.dumps({
        "t": "EXEC", "action": "ApplyPatch:a"}) + "\n", encoding="utf-8")
    assert resume_suite_evidence(episode) == {}


@needs_git
def test_end_to_end_repo_episode(repo, tmp_path):
    episode_dir = tmp_path / "ep1"
    queue_fix(episode_dir)

    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD)

    assert result.success, result.plan
    assert "ApplyPatch:fix-001" in result.plan
    assert result.plan[-1] == "Commit"
    assert result.llm_planning_calls == 0
    assert executor.incidents == 0

    # The optimistic RunSuite contract was corrected by reality at least once.
    entries = Journal.read(journal_path)
    assert any(e["t"] == "OBSERVE" and e["diverged"] for e in entries)
    # Execution evidence is journaled with gates and artifact hashes.
    assert any(e["t"] == "GATE" and e["approved"] for e in entries)
    assert any(e["t"] == "EXEC" and e["artifacts"] for e in entries)

    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err
    rr = replay(str(journal_path))
    assert rr.ok, rr.mismatches

    # The fix really landed: file content and a commit on the episode branch.
    worktree = episode_dir / "worktree"
    assert (worktree / "calc.py").read_text(encoding="utf-8") == FIXED
    log = subprocess.run(["git", "log", "--oneline"], cwd=worktree,
                         capture_output=True, text=True, check=True).stdout
    assert "ledger: apply queued fix" in log


@needs_git
def test_goal_compiled_episode(repo, tmp_path):
    """M3: natural-language task -> vocabulary-validated goal -> episode.
    The compiled contract is journaled in the HEADER."""
    episode_dir = tmp_path / "ep_goal"
    queue_fix(episode_dir)
    backend = FakeBackend(json.dumps({
        "goal": ["tests.pass:unit", "repo.clean"],
        "rationale": "make the suite green and leave the tree committed",
    }))

    result, _, journal_path = run_repo_episode(
        repo, episode_dir, None, TEST_CMD,
        task_text="Fix the failing unit test and commit the fix.",
        backend=backend)

    assert result.success
    header = Journal.read(journal_path)[0]
    assert header["goal"] == ["repo.clean", "tests.pass:unit"]
    assert header["task_text"].startswith("Fix the failing")
    assert header["goal_backend"] == "fake"
    assert replay(str(journal_path)).ok
