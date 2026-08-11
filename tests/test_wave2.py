"""Wave-2 parity: secret redaction, PushBranch (network-gated publishing),
and `ledger run --json` structured output."""

import json
import subprocess

from conftest import GOAL, TEST_CMD, needs_git, queue_fix

import ledger.cli as cli
from ledger.executor import (BlobStore, PolicyGate, redact_secrets,
                             run_repo_episode)
from ledger.journal import Journal
from ledger.llm import FakeBackend

FIX_DIFF = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n-    return a - b\n+    return a + b\n")


# -- redaction ---------------------------------------------------------------

def test_redact_secrets_patterns():
    text = (
        "Authorization: Bearer abc123def456ghi789jkl\n"
        "token ghp_abcdefghijklmnopqrstuvwxyz123456\n"
        "key sk-proj_abcdefghijklmnop\n"
        "aws AKIAIOSFODNN7EXAMPLE\n"
        "password=hunter2hunter2\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----\n"
        "normal output stays: 3 failed, 2 passed\n"
    )
    out = redact_secrets(text)
    assert "abc123def456" not in out
    assert "ghp_" not in out
    assert "sk-proj" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "hunter2" not in out
    assert "MIIEow" not in out
    assert "[REDACTED PRIVATE KEY]" in out
    assert "3 failed, 2 passed" in out  # evidence survives


def test_blobstore_stores_redacted(tmp_path):
    blobs = BlobStore(tmp_path / "blobs")
    sha = blobs.put("export API_KEY=supersecretvalue1 done")
    stored = (tmp_path / "blobs" / f"{sha}.txt").read_text(encoding="utf-8")
    assert "supersecretvalue1" not in stored
    assert "[REDACTED]" in stored


# -- PushBranch --------------------------------------------------------------

def _bare_origin(repo, tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                   capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo,
                   check=True, capture_output=True)
    return bare


@needs_git
def test_push_branch_publishes_episode_branch(repo, tmp_path):
    bare = _bare_origin(repo, tmp_path)
    episode_dir = tmp_path / "pushed"
    queue_fix(episode_dir)
    goal = GOAL | {"repo.pushed:ledger/pushed"}
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, goal, TEST_CMD, push=True)

    assert result.success, result.plan
    assert result.plan[-1] == "PushBranch"
    assert executor.incidents == 0
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify",
         "refs/heads/ledger/pushed"], capture_output=True, text=True)
    assert proc.returncode == 0  # origin really has the branch
    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err


@needs_git
def test_supervised_profile_gates_the_push(repo, tmp_path):
    """network risk stops under supervised: with no human at the terminal
    the push is denied, nothing reaches origin, and the episode escalates."""
    bare = _bare_origin(repo, tmp_path)
    episode_dir = tmp_path / "gated"
    queue_fix(episode_dir)
    goal = GOAL | {"repo.pushed:ledger/gated"}
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, goal, TEST_CMD, push=True,
        gate=PolicyGate("supervised"), profile="supervised")

    assert not result.success
    assert executor.denials > 0
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify",
         "refs/heads/ledger/gated"], capture_output=True, text=True)
    assert proc.returncode != 0  # nothing was published
    entries = Journal.read(str(journal_path))
    denied = [e for e in entries if e["t"] == "GATE" and not e["approved"]]
    assert denied and all(e["action"] == "PushBranch" for e in denied)
    esc = [e for e in entries if e["t"] == "ESCALATION"]
    assert esc and esc[0]["reason"] == "gate_denied"


# -- run --json --------------------------------------------------------------

@needs_git
def test_run_json_output(repo, monkeypatch, capsys):
    def responder(prompt: str) -> str:
        if "REPOSITORY FILES" in prompt:
            return FIX_DIFF
        return json.dumps({"goal": sorted(GOAL),
                           "rationale": "green and committed"})

    monkeypatch.setattr(cli, "_backend",
                        lambda args: FakeBackend(responder))
    d = repo / ".ledger"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"test_cmd": TEST_CMD}),
                                   encoding="utf-8")
    rc = cli.main(["run", "fix the failing test", "--repo", str(repo),
                   "--json", "--yes"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["success"] is True
    assert out["escalation"] is None
    assert out["plan"][-1] == "Commit"
    assert out["incidents"] == 0 and out["denials"] == 0
    assert sorted(out["goal"]) == sorted(GOAL)
    assert out["journal"].endswith("episode.jsonl")
