"""LLM patch authoring: diff extraction, git-apply validation, retry, and a
full episode where LEDGER writes the fix itself instead of being handed one."""

import difflib
import json

import pytest
from conftest import BUGGY, FIXED, GOAL, TEST_CMD, needs_git

from ledger.executor import BlobStore, ExecContext, run_repo_episode
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.replay import replay
from ledger.sensors import ExpensiveResultCache
from ledger.skills_llm import (AuthorPatchSkill, build_authoring_catalog,
                               repair_hunk_counts,
                               extract_diff)

FIX_DIFF = "".join(difflib.unified_diff(
    BUGGY.splitlines(keepends=True), FIXED.splitlines(keepends=True),
    fromfile="a/calc.py", tofile="b/calc.py"))


def responder(prompt: str) -> str:
    """One fake backend serving both the goal compiler and the patch author."""
    if "REPOSITORY FILES" in prompt:
        return FIX_DIFF
    return json.dumps({"goal": sorted(GOAL), "rationale": "green and committed"})


# -- extract_diff ------------------------------------------------------------

def test_extract_bare_diff():
    assert extract_diff(FIX_DIFF) == FIX_DIFF


def test_extract_fenced_diff():
    wrapped = f"Here you go:\n\n```diff\n{FIX_DIFF}```\n"
    assert extract_diff(wrapped) == FIX_DIFF


def test_extract_strips_leading_prose():
    out = extract_diff("I'll fix the sign error.\n\n" + FIX_DIFF)
    assert out.startswith("--- a/calc.py")


def test_extract_returns_none_without_a_diff():
    assert extract_diff("I would change the minus to a plus.") is None


# -- the skill ---------------------------------------------------------------

def _ctx(repo, tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    return ExecContext(worktree=repo, blobs=BlobStore(tmp_path / "blobs"),
                       test_cache=ExpensiveResultCache(), queue_dir=queue,
                       applied_dir=tmp_path / "applied")


@needs_git
def test_author_patch_queues_a_valid_diff(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    skill = AuthorPatchSkill("a", FakeBackend(FIX_DIFF), "fix add()")
    result = skill.run(ctx)

    assert result.ok
    assert result.claimed_props == frozenset({"patch.queued:a"})
    assert (ctx.queue_dir / "a.patch").exists()
    assert "diff" in result.artifacts


@needs_git
def test_author_patch_rejects_an_unappliable_diff(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    bogus = ("--- a/nonexistent.py\n+++ b/nonexistent.py\n"
             "@@ -1 +1 @@\n-nope\n+nah\n")
    skill = AuthorPatchSkill("a", FakeBackend(bogus), "fix add()")
    result = skill.run(ctx)

    assert not result.ok
    assert not (ctx.queue_dir / "a.patch").exists()
    # Nothing is claimed, so the planner sees a plain non-effect.
    assert result.claimed_props == frozenset()
    assert any("rejected" in c for c in result.claims)


@needs_git
def test_author_patch_retries_after_rejection(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    calls = {"n": 0}

    def flaky(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "no diff here, sorry"
        return FIX_DIFF

    result = AuthorPatchSkill("a", FakeBackend(flaky), "fix add()").run(ctx)
    assert result.ok
    assert calls["n"] == 2
    assert (ctx.queue_dir / "a.patch").exists()


def test_no_leftover_candidate_files(tmp_path, repo):
    ctx = _ctx(repo, tmp_path)
    AuthorPatchSkill("a", FakeBackend(FIX_DIFF), "fix").run(ctx)
    assert not list(ctx.queue_dir.glob(".*.candidate"))


# -- catalog -----------------------------------------------------------------

def test_authoring_catalog_shape():
    cat = build_authoring_catalog(["a", "b"])
    names = {a.name for a in cat}
    assert {"AuthorPatch:a", "ApplyPatch:a", "AuthorPatch:b",
            "ApplyPatch:b", "RunSuite", "Commit"} <= names
    author = cat.get("AuthorPatch:a")
    # Authoring is stochastic: the model may fail to produce an appliable diff.
    assert not author.deterministic
    assert author.add == frozenset({"patch.queued:a"})
    # Applying consumes the queued patch.
    assert "patch.queued:a" in cat.get("ApplyPatch:a").delete


def test_authoring_catalog_requires_slots():
    with pytest.raises(ValueError):
        build_authoring_catalog([])


# -- end to end --------------------------------------------------------------

@needs_git
def test_ledger_authors_its_own_fix(repo, tmp_path):
    """The gap M4 was meant to close: no patch is queued up front -- the
    agent writes one, applies it, re-tests and commits."""
    episode_dir = tmp_path / "authored"
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, None, TEST_CMD,
        task_text="Fix the failing unit test and commit.",
        backend=FakeBackend(responder), slots=["a"])

    assert result.success, result.plan
    assert "AuthorPatch:a" in result.plan
    assert "ApplyPatch:a" in result.plan
    assert result.plan[-1] == "Commit"
    # The planner still made zero LLM calls to decide what to do.
    assert result.llm_planning_calls == 0
    assert executor.incidents == 0

    assert (episode_dir / "worktree" / "calc.py").read_text(
        encoding="utf-8") == FIXED
    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err
    assert replay(str(journal_path)).ok


NEW_FILE_MISCOUNTED = (
    "diff --git a/new_mod.py b/new_mod.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/new_mod.py\n"
    "@@ -0,0 +1,999 @@\n"           # the model claimed 999; there are 3
    "+def hello():\n"
    "+    return 'hi'\n"
    "+\n"
)


def test_repair_hunk_counts_fixes_the_arithmetic():
    fixed = repair_hunk_counts(NEW_FILE_MISCOUNTED)
    assert "@@ -0,0 +1,3 @@" in fixed   # a bare "+" is an added blank line
    assert "def hello():" in fixed


def test_repair_leaves_a_correct_diff_alone():
    good = (
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n-    return a - b\n+    return a + b\n")
    assert repair_hunk_counts(good).strip() == good.strip()


@needs_git
def test_a_miscounted_patch_is_repaired_and_queued(repo, tmp_path):
    """Regression: a local model wrote a complete, correct 273-line file but
    announced 342 lines in the hunk header. git called the whole patch
    corrupt and four authoring attempts were thrown away. The counts are
    derivable from the body, so they get recomputed -- and the result still
    has to pass `git apply --check`."""
    ctx = _ctx(repo, tmp_path)
    result = AuthorPatchSkill(
        "a", FakeBackend(NEW_FILE_MISCOUNTED), "add a module").run(ctx)

    assert result.ok, result.claims
    assert (ctx.queue_dir / "a.patch").exists()
    assert any("repaired" in str(c) for c in result.claims)
