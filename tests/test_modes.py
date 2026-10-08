"""The three research modes have different stopping and acceptance rules."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ledger.modes import (Attempt, PLAN_SECTIONS, command_acceptor, create_plan,
                          run_until)


def test_plan_writes_required_design_sections_without_overwriting(tmp_path):
    class Backend:
        def complete(self, prompt):
            assert "candidate must not rewrite" in prompt
            return "\n\n".join(f"## {s}\nSpecific design." for s in PLAN_SECTIONS)

    path = tmp_path / "plan.md"
    assert create_plan("research task", Backend(), path) == path
    assert "research task" in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        create_plan("another task", Backend(), path)


def test_incomplete_plan_is_not_written(tmp_path):
    class Backend:
        def complete(self, prompt):
            return "## Architecture\nToo little"

    path = tmp_path / "plan.md"
    with pytest.raises(ValueError, match="omitted plan sections"):
        create_plan("task", Backend(), path)
    assert not path.exists()


def test_goal_retries_failed_or_rejected_candidate(tmp_path):
    calls = []

    def episode(instruction, number):
        calls.append((instruction, number))
        return Attempt(number > 1, tmp_path, tmp_path / "episode.jsonl")

    checks = []

    def accept(attempt):
        checks.append(attempt)
        return len(checks) == 2

    attempts = run_until("solve", episode, mode="goal", accept=accept,
                         max_iterations=4)
    assert len(attempts) == 3
    assert len(checks) == 2  # never check a failed episode
    assert "previous episode journal" in calls[1][0]


def test_loop_continues_after_episode_success(tmp_path):
    attempts = run_until(
        "refine", lambda task, n: Attempt(True, tmp_path,
                                            tmp_path / "journal.jsonl"),
        mode="loop", max_iterations=3)
    assert len(attempts) == 3
    with pytest.raises(ValueError, match="independent acceptance"):
        run_until("goal", lambda *_: attempts[0], mode="goal")


def test_backend_down_backoff_grows_and_resets(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("ledger.modes.time.sleep", sleeps.append)
    # down, down, down, up, down: backoff should grow for the first three,
    # reset on the recovery, then start over from the base delay.
    down_sequence = iter([True, True, True, False, True])

    def episode(instruction, number):
        return Attempt(False, tmp_path, tmp_path / "episode.jsonl")

    run_until("task", episode, mode="loop", max_iterations=5,
             pause_seconds=5, backend_down=lambda _: next(down_sequence))

    assert sleeps == [30, 60, 120, 5, 30]


def test_acceptance_is_separate_from_hash_chained_episode(tmp_path):
    journal = tmp_path / "episode.jsonl"
    journal.write_text("original chain\n", encoding="utf-8")
    attempt = Attempt(True, tmp_path, journal)
    checker = command_acceptor(
        f'{sys.executable} -c "import sys; sys.exit(0)"', tmp_path)
    assert checker(attempt)
    assert journal.read_text(encoding="utf-8") == "original chain\n"
    event = json.loads((tmp_path / "acceptance.jsonl").read_text(
        encoding="utf-8"))
    assert event["exit_code"] == 0
    assert event["candidate"] == str(tmp_path)


def test_acceptance_timeout_is_rejection_with_evidence(tmp_path, monkeypatch):
    journal = tmp_path / "episode.jsonl"
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("checker", 2)
    monkeypatch.setattr(subprocess, "run", timeout)
    accept = command_acceptor("checker", tmp_path, timeout=2)
    assert not accept(Attempt(True, tmp_path, journal))
    record = json.loads((tmp_path / "acceptance.jsonl").read_text(
        encoding="utf-8"))
    assert record["exit_code"] == -1


def test_iterative_episode_requires_new_patch_even_on_clean_repo(repo, tmp_path):
    from conftest import TEST_CMD
    from ledger.executor import run_repo_episode
    from ledger.journal import Journal
    from ledger.llm import FakeBackend

    def responder(prompt):
        if "REPOSITORY FILES" not in prompt:
            raise AssertionError("fixed iterative goal must not be model-compiled")
        return "FILE: calc.py\n```\ndef add(a, b):\n    return a + b\n```"

    result, _, journal = run_repo_episode(
        repo, tmp_path / "episode", None, TEST_CMD,
        task_text="repair the calculator", backend=FakeBackend(responder),
        slots=["iteration1"], required_patch_slot="iteration1",
        product_review=False)
    assert result.success
    assert "patch.applied:iteration1" in Journal.read(str(journal))[0]["goal"]


def test_research_cli_keeps_model_answer_provisional(tmp_path, monkeypatch, capsys):
    from ledger import cli
    from ledger.lab_contracts import EvidenceStore
    from ledger.llm import FakeBackend

    model = FakeBackend(json.dumps({"answer": "conjecture Σ", "assumptions": [],
                                    "verification_plan": "use a pinned checker",
                                    "status": "formally_checked"}))
    monkeypatch.setattr(cli, "_backend", lambda *_: model)
    assert cli.main(["research", "prove P", "--domain", "mathematics",
                     "--repo", str(tmp_path), "--id", "theorem-1"]) == 0
    assert '"status": "hypothesized"' in capsys.readouterr().out
    records = EvidenceStore(tmp_path / ".ledger" / "research" /
                            "evidence.jsonl").read()
    assert [entry["kind"] for entry in records] == ["problem", "proposal"]


def test_loop_cli_uses_new_slots_and_resumes_same_worktree(tmp_path, monkeypatch):
    from ledger import cli
    seen = []

    def fake_run(args):
        seen.append((args.slots, args.require_patch_slot, args.resume))
        return 0

    monkeypatch.setattr(cli, "cmd_run", fake_run)
    assert cli.main(["loop", "refine", "--repo", str(tmp_path),
                     "--name", "trial", "--max-iterations", "2"]) == 0
    assert seen == [("iteration1", "iteration1", False),
                    ("iteration2", "iteration2", True)]


def test_iteration_backend_down_requires_every_author_call_to_have_failed_transport(tmp_path):
    from ledger.cli import _iteration_backend_down

    def journal_with(name, events):
        path = tmp_path / f"{name}.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                        encoding="utf-8")
        return Attempt(False, tmp_path, path)

    all_down = journal_with("all_down", [
        {"t": "EXEC", "action": "AuthorPatch:a", "ok": False,
         "claims": ["attempt 1: backend error: HTTP 500: out of memory"]},
        {"t": "EXEC", "action": "AuthorPatch:a", "ok": False,
         "claims": ["attempt 2: backend unavailable (health check failed)"]},
    ])
    assert _iteration_backend_down(all_down)

    mixed = journal_with("mixed", [
        {"t": "EXEC", "action": "AuthorPatch:a", "ok": False,
         "claims": ["attempt 1: backend error: HTTP 500: out of memory"]},
        {"t": "EXEC", "action": "AuthorPatch:a", "ok": False,
         "claims": ["attempt 2: response contained neither FILE: blocks "
                    "nor a unified diff"]},
    ])
    assert not _iteration_backend_down(mixed)

    no_author_calls = journal_with("no_author_calls", [
        {"t": "EXEC", "action": "RunSuite", "ok": False,
         "claims": ["unit suite failing"]},
    ])
    assert not _iteration_backend_down(no_author_calls)

    assert not _iteration_backend_down(Attempt(False, tmp_path,
                                               tmp_path / "missing.jsonl"))


def test_resource_exhaustion_retry_resumes_from_invocation_directory(
        tmp_path, monkeypatch):
    from ledger import cli

    def fake_run(args):
        raise OSError("[WinError 1455] The paging file is too small for "
                      "this operation")
    monkeypatch.setattr(cli, "cmd_run", fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda *_: None)
    captured = {}

    def fake_subprocess_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(cli.subprocess, "run", fake_subprocess_run)
    monkeypatch.chdir(tmp_path)

    # A relative --repo is meaningful only from the invocation directory.
    # Regression: the retry respawn used to pin cwd to the Ledger package's
    # own source root, silently redirecting "." there.
    assert cli.main(["loop", "refine", "--repo", ".", "--name", "trial",
                     "--self-heal", "--max-iterations", "1"]) == 0
    assert captured.get("cwd") is None  # inherits the invocation directory


def test_self_heal_resume_respawns_from_invocation_directory(
        tmp_path, monkeypatch):
    from ledger import cli, self_heal

    def fake_run(args):
        raise RuntimeError("boom")
    monkeypatch.setattr(cli, "cmd_run", fake_run)
    monkeypatch.setattr(self_heal, "try_repair", lambda *a, **k: True)
    captured = {}

    def fake_subprocess_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(cli.subprocess, "run", fake_subprocess_run)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["loop", "refine", "--repo", ".", "--name", "trial",
                     "--self-heal", "--max-iterations", "1"]) == 0
    assert captured.get("cwd") is None  # inherits the invocation directory
