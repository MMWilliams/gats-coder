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
                               diff_from_files, extract_diff,
                               extract_files, failure_source_context)

FIX_DIFF = "".join(difflib.unified_diff(
    BUGGY.splitlines(keepends=True), FIXED.splitlines(keepends=True),
    fromfile="a/calc.py", tofile="b/calc.py"))


def test_failure_context_includes_traceback_line_beyond_file_head(tmp_path):
    source = tmp_path / "scene.py"
    source.write_text("\n".join(f"line {i}" for i in range(1, 501)),
                      encoding="utf-8")
    output = f'File "{source}", line 423, in build_scene\nTypeError: wrong type'
    context = failure_source_context(tmp_path, output)
    assert "423: line 423" in context
    assert "413: line 413" in context


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


class _FlakyBackend:
    """Raises a backend-down error once, then answers `health` for the
    fail-fast probe before the next attempt."""

    id = "flaky"

    def __init__(self, health):
        self.calls = 0
        self.health_calls = 0
        self._health = health

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(
                "http://x/chat -> HTTP 500: cudaMalloc failed: out of memory")
        return FIX_DIFF

    def health_check(self) -> bool:
        self.health_calls += 1
        return self._health


@needs_git
def test_author_patch_skips_retry_when_backend_stays_down(repo, tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr("ledger.skills_llm.time.sleep", lambda *_: None)
    ctx = _ctx(repo, tmp_path)
    backend = _FlakyBackend(health=False)
    result = AuthorPatchSkill("a", backend, "fix add()").run(ctx)

    assert not result.ok
    assert backend.calls == 1          # never re-sent the prompt
    assert backend.health_calls == 1
    assert any("backend error" in c for c in result.claims)
    assert any("health check failed" in c for c in result.claims)


@needs_git
def test_author_patch_retries_once_backend_health_recovers(repo, tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr("ledger.skills_llm.time.sleep", lambda *_: None)
    ctx = _ctx(repo, tmp_path)
    backend = _FlakyBackend(health=True)
    result = AuthorPatchSkill("a", backend, "fix add()").run(ctx)

    assert result.ok
    assert backend.calls == 2
    assert backend.health_calls == 1
    assert (ctx.queue_dir / "a.patch").exists()


@needs_git
def test_author_patch_rejects_diff_text_inside_python_file(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    calls = {"n": 0}

    def respond(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "FILE: calc.py\n```python\n@@ -1 +1 @@\n-bad\n+good\n```"
        return FIX_DIFF

    result = AuthorPatchSkill("a", FakeBackend(respond), "fix").run(ctx)
    assert result.ok
    assert calls["n"] == 2
    assert any("Python syntax error" in claim for claim in result.claims)


@needs_git
def test_author_patch_rejects_large_rewrite_after_suite_failure(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    # The cap guards existing lines while other tests pass: pad the module
    # past the 24-line floor so a wholesale rewrite removes more than that.
    with (repo / "calc.py").open("a", encoding="utf-8") as f:
        f.write("".join(f"\n# note {i}" for i in range(60)) + "\n")
    ctx.suite_evidence["unit"] = {"tail": "1 failed, 3 passed in 0.1s"}
    calls = {"n": 0}

    def respond(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            body = "\n".join(f"value_{i} = {i}" for i in range(40))
            return f"FILE: calc.py\n```python\n{body}\n```"
        return FIX_DIFF

    result = AuthorPatchSkill("a", FakeBackend(respond), "fix").run(ctx)
    assert result.ok
    assert calls["n"] == 2
    assert any("localized test failure" in claim for claim in result.claims)


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
    # Every code change invalidates the prior suite result and permits a rerun.
    assert "tests.unknown:unit" in cat.get("ApplyPatch:a").add


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
    assert result.plan[result.plan.index("ApplyPatch:a") + 1] == "RunSuite"
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


# -- whole-file authoring ----------------------------------------------------

FILE_RESPONSE = """Here is the module.

FILE: calc.py
```python
def add(a, b):
    return a + b
```
"""


def test_extract_files_parses_blocks():
    files = extract_files(FILE_RESPONSE)
    assert files == [("calc.py", "def add(a, b):\n    return a + b\n")]


def test_extract_files_refuses_to_escape_the_worktree():
    """A hostile path is rejected, never repaired.

    The first version normalised before validating, so `lstrip("./")` turned
    "../../etc/passwd" into "etc/passwd" and the traversal check saw a clean
    relative path -- the sanitiser defeated the guard.
    """
    for hostile in ("../../etc/passwd", "/etc/passwd", "~/.ssh/id_rsa",
                    "C:/Windows/win.ini", "..\\..\\Windows\\win.ini",
                    "src/../../out.txt"):
        assert extract_files(f"FILE: {hostile}\n```\nx\n```") == [], hostile
    # an ordinary path still works, with a leading ./ tidied away
    assert extract_files("FILE: ./src/a.js\n```\nx\n```") == \
        [("src/a.js", "x\n")]


@needs_git
def test_diff_from_files_rewrites_an_existing_file(repo):
    diff = diff_from_files(repo, [("calc.py", FIXED)])
    assert "--- a/calc.py" in diff and "+++ b/calc.py" in diff
    assert "+    return a + b" in diff


@needs_git
def test_diff_from_files_creates_a_new_file(repo):
    diff = diff_from_files(repo, [("brand/new.py", "print('hi')\n")])
    assert "new file mode" in diff and "--- /dev/null" in diff


@needs_git
def test_no_diff_when_the_content_is_unchanged(repo):
    same = (repo / "calc.py").read_text(encoding="utf-8")
    assert diff_from_files(repo, [("calc.py", same)]) == ""


@needs_git
def test_whole_file_response_is_queued_as_a_patch(repo, tmp_path):
    """The unlock for smaller models: they return the file they can write
    correctly, and LEDGER constructs the diff -- which still has to pass
    `git apply --check` before it is queued."""
    ctx = _ctx(repo, tmp_path)
    result = AuthorPatchSkill(
        "a", FakeBackend(FILE_RESPONSE), "fix add()").run(ctx)

    assert result.ok, result.claims
    queued = (ctx.queue_dir / "a.patch").read_text(encoding="utf-8")
    assert "calc.py" in queued
    assert any("built the patch from" in str(c) for c in result.claims)
