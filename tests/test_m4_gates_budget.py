"""M4: autonomy profiles, risk gates, denial handling, and the LLM
authoring budget (design SS7.4, SS8.1, SS12.3)."""

import json

import pytest
from conftest import GOAL, TEST_CMD, needs_git, queue_fix

from ledger.executor import (BudgetMeter, GATED_RISKS, PolicyGate,
                             attended_gate, run_repo_episode, tty_approver)
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.replay import replay
from ledger.sensors import budget_sensor


# -- PolicyGate --------------------------------------------------------------

def test_profile_matrix():
    approvals = []

    def yes(name, risk):
        approvals.append((name, risk))
        return True

    for profile, gated in GATED_RISKS.items():
        for risk in ("read", "write", "exec", "network", "irreversible"):
            approvals.clear()
            gate = PolicyGate(profile, approver=yes)
            gate(f"X:{risk}", risk)
            assert bool(approvals) == (risk in gated), (profile, risk)


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        PolicyGate("cowboy")


def test_gate_remembers_per_action_family():
    calls = []

    def approver(name, risk):
        calls.append(name)
        return True

    gate = PolicyGate("attended", approver=approver)
    assert gate("AuthorPatch:a", "write")
    assert gate("AuthorPatch:b", "write")   # same family, no second ask
    assert gate("Commit", "write")          # new family, asks again
    assert calls == ["AuthorPatch:a", "Commit"]


def test_tty_approver_fails_closed_without_terminal():
    # Under pytest stdin is not a terminal: no human, no approval.
    assert tty_approver("Commit", "write") is False


# -- denial is survivable ----------------------------------------------------

@needs_git
def test_denied_episode_escalates_instead_of_crashing(repo, tmp_path):
    """attended profile with nobody at the terminal: every gated action is
    denied, the planner routes until nothing is left, and the journal ends
    in an ESCALATION -- never an exception."""
    episode_dir = tmp_path / "denied"
    queue_fix(episode_dir)
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD,
        gate=PolicyGate("attended"), profile="attended")

    assert not result.success
    assert executor.denials > 0
    entries = Journal.read(str(journal_path))
    gates = [e for e in entries if e["t"] == "GATE"]
    assert gates and all(not g["approved"] for g in gates)
    assert all(g["profile"] == "attended" for g in gates)
    esc = [e for e in entries if e["t"] == "ESCALATION"]
    assert len(esc) == 1 and esc[0]["reason"] == "gate_denied"
    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err


@needs_git
def test_a_denial_does_not_outlive_the_session_that_gave_it(repo, tmp_path):
    """Deny everything, then resume with an approving gate and let it work.

    SS8.1 routes around a "no" by observing a self-loop, which used to be
    learned: written into episode_dir/graph.sqlite and the repo's shared
    l2.sqlite, reloaded on the warm start, and pruned before the resumed
    episode took a single step. The human changed their mind and the
    planner could not hear it -- attempt 2 escalated no_path at 0 steps.
    """
    episode_dir = tmp_path / "denied-then-allowed"
    queue_fix(episode_dir)
    l2 = tmp_path / "l2.sqlite"

    first, executor, _ = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD, gate=PolicyGate("attended"),
        profile="attended", l2_path=str(l2))
    assert not first.success
    assert executor.blocked, "a denied action must be blocked for the session"

    second, _, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD, profile="attended",
        gate=PolicyGate("attended", approver=attended_gate),
        l2_path=str(l2), resume=True)
    assert second.success, second.plan
    assert "ApplyPatch:fix-001" in second.plan

    # Nothing in the resumed journal claims the refusals were divergences.
    entries = Journal.read(str(journal_path))
    assert not any(e["t"] == "OBSERVE" and e.get("denied") for e in entries)


@needs_git
def test_a_refusal_is_recorded_but_never_learned(repo, tmp_path):
    """The refusal is in the journal as a refusal, and the world model is
    untouched by it: no OBSERVE claims divergence, nothing was recorded."""
    episode_dir = tmp_path / "refused"
    queue_fix(episode_dir)
    result, _, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD, gate=PolicyGate("attended"),
        profile="attended")

    assert not result.success
    entries = Journal.read(str(journal_path))
    refused = [e for e in entries if e["t"] == "OBSERVE" and e.get("denied")]
    assert refused, "a denied step must be journaled as denied"
    assert all(not e["diverged"] for e in refused)
    # Replay reproduces the search, which it can only do if it rebuilds the
    # same set of unavailable actions from the record.
    assert replay(str(journal_path)).ok


@needs_git
def test_autonomous_profile_needs_no_human(repo, tmp_path):
    """autonomous gates only irreversible actions; this catalog has none,
    so the same episode succeeds with zero approver involvement."""
    episode_dir = tmp_path / "auto"
    queue_fix(episode_dir)

    def never(name, risk):  # would fail the test if ever consulted
        raise AssertionError("approver consulted under autonomous profile")

    result, executor, _ = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD,
        gate=PolicyGate("autonomous", approver=never), profile="autonomous")
    assert result.success
    assert executor.denials == 0


# -- budget ------------------------------------------------------------------

def test_budget_meter_and_sensor(tmp_path):
    meter = BudgetMeter(2, resource="llm")
    sensor = budget_sensor(meter)
    assert sensor.probe(tmp_path) == {"budget.llm:ok"}
    meter.charge()
    assert not meter.exhausted
    meter.charge()
    assert meter.exhausted
    assert sensor.probe(tmp_path) == set()


@needs_git
def test_author_budget_exhaustion_escalates(repo, tmp_path):
    """A backend that never produces a diff burns its per-episode budget and
    the episode ends in ESCALATION(budget_exhausted) -- not an infinite
    author/reject loop (design SS12.3)."""
    episode_dir = tmp_path / "broke"
    backend = FakeBackend("I would simply fix the bug.")  # never a diff
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, frozenset({"tests.pass:unit"}), TEST_CMD,
        backend=backend, slots=["a", "b"], max_author_attempts=3)

    assert not result.success
    entries = Journal.read(str(journal_path))
    esc = [e for e in entries if e["t"] == "ESCALATION"]
    assert len(esc) == 1
    assert esc[0]["reason"] == "budget_exhausted"
    assert esc[0]["author_attempts"] == 3
    assert esc[0]["author_attempt_limit"] == 3
    # The budget proposition vanished from the final observed state.
    last_obs = [e for e in entries if e["t"] == "OBSERVE"][-1]
    assert "budget.llm:ok" not in last_obs["obs_props"]
    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err


@needs_git
def test_multi_slot_episode_authors_two_fixes(repo, tmp_path):
    """Two independent bugs, one episode: the agent authors a patch per slot,
    guided by which test is failing *now* (suite evidence in the prompt).
    Exercises the multi-slot catalog end to end."""
    (repo / "mul.py").write_text("def mul(a, b):\n    return a + b\n",
                                 encoding="utf-8")
    (repo / "test_mul.py").write_text(
        "from mul import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n",
        encoding="utf-8")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "second bug"], cwd=repo, check=True,
                   capture_output=True)

    ADD_DIFF = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                " def add(a, b):\n-    return a - b\n+    return a + b\n")
    MUL_DIFF = ("--- a/mul.py\n+++ b/mul.py\n@@ -1,2 +1,2 @@\n"
                " def mul(a, b):\n-    return a + b\n+    return a * b\n")

    def failing_section(prompt: str) -> str:
        if "MOST RECENT FAILING TEST OUTPUT" not in prompt:
            return ""
        seg = prompt.split("MOST RECENT FAILING TEST OUTPUT", 1)[1]
        return seg.split("RELEVANT FILE CONTENTS", 1)[0]

    def responder(prompt: str) -> str:
        if "REPOSITORY FILES" not in prompt:
            return json.dumps({"goal": ["tests.pass:unit"],
                               "rationale": "suite green"})
        evidence = failing_section(prompt)
        if "test_add" in evidence:
            return ADD_DIFF
        if "test_mul" in evidence:
            return MUL_DIFF
        return "no failing evidence, no diff"

    result, executor, journal_path = run_repo_episode(
        repo, tmp_path / "two", None, TEST_CMD,
        task_text="fix both failing unit tests",
        backend=FakeBackend(responder), slots=["a", "b"])

    assert result.success, result.plan
    authored = [a for a in result.plan if a.startswith("AuthorPatch:")]
    applied = [a for a in result.plan if a.startswith("ApplyPatch:")]
    # Two bugs cannot fall to one patch here: the agent must author, apply,
    # re-test, and author again (slots are interchangeable containers, so
    # which slot carries which fix is the planner's business).
    assert len(authored) >= 2, result.plan
    assert len(applied) >= 2, result.plan
    assert executor.incidents == 0
    worktree = tmp_path / "two" / "worktree"
    assert "a + b" in (worktree / "calc.py").read_text(encoding="utf-8")
    assert "a * b" in (worktree / "mul.py").read_text(encoding="utf-8")
    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err


@needs_git
def test_both_slots_can_apply_in_one_episode(repo, tmp_path):
    """The catalog's multi-slot claim, exercised directly: a goal that
    requires patch.applied:a AND patch.applied:b forces both slots through
    ApplyPatch in a single episode (one authored, one pre-queued)."""
    (repo / "mul.py").write_text("def mul(a, b):\n    return a + b\n",
                                 encoding="utf-8")
    (repo / "test_mul.py").write_text(
        "from mul import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n",
        encoding="utf-8")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "second bug"], cwd=repo, check=True,
                   capture_output=True)

    episode_dir = tmp_path / "both"
    queue = episode_dir / "queue"
    queue.mkdir(parents=True)
    (queue / "b.patch").write_text(
        "--- a/mul.py\n+++ b/mul.py\n@@ -1,2 +1,2 @@\n"
        " def mul(a, b):\n-    return a + b\n+    return a * b\n",
        encoding="utf-8")
    ADD_DIFF = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                " def add(a, b):\n-    return a - b\n+    return a + b\n")

    goal = frozenset({"tests.pass:unit", "patch.applied:a",
                      "patch.applied:b"})
    result, executor, _ = run_repo_episode(
        repo, episode_dir, goal, TEST_CMD,
        backend=FakeBackend(ADD_DIFF), slots=["a", "b"])

    assert result.success, result.plan
    assert "ApplyPatch:a" in result.plan
    assert "ApplyPatch:b" in result.plan
    assert executor.incidents == 0
