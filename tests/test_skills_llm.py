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
                               extract_files, import_errors, syntax_errors)

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
    # A red suite is never re-run on the same content: seen live, the
    # optimistic prior used to buy a second identical run before authoring.
    assert result.plan[:2] == ["RunSuite", "AuthorPatch:a"], result.plan
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


# -- syntax gate -------------------------------------------------------------

def test_syntax_errors_catches_the_chess_typo():
    """The real slip from the chess loop, r69: it cost a whole round."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    bad = ("import * as THREE from 'three';\nexport class S {\n"
           "  init() {\n    this/renderer = new THREE.WebGLRenderer();\n"
           "  }\n}\n")
    errs = syntax_errors([("src/scene.js", bad)])
    assert len(errs) == 1 and "src/scene.js" in errs[0]
    good = bad.replace("this/renderer", "this.renderer")
    assert syntax_errors([("src/scene.js", good)]) == []


def test_syntax_errors_python_and_json():
    assert syntax_errors([("a.py", "def f(:\n")])[0].startswith("a.py:1")
    assert syntax_errors([("a.json", "{\"a\": }")])
    assert syntax_errors([("a.py", "x = 1\n"), ("b.json", "{}"),
                          ("c.gd", "anything")]) == []


@needs_git
def test_a_file_that_does_not_parse_is_sent_back(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    prompts: list[str] = []

    def author(prompt: str) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return FILE_RESPONSE.replace("return a + b", "return a +")
        return FILE_RESPONSE

    result = AuthorPatchSkill("a", FakeBackend(author), "fix add()").run(ctx)
    assert result.ok, result.claims
    assert "would not build" in prompts[1] and "calc.py" in prompts[1]
    assert any(c["cmd"] == "syntax check" for c in result.commands)


def test_failure_excerpt_keeps_the_error_not_the_frames():
    from ledger.executor import failure_excerpt
    out = ("\x1b[31merror during build:\n"
           "src/scene.js (22:18): Expected ';'\x1b[39m\n"
           + "".join(f"    at f{i} (file:///w/node_modules/rollup/x.js:{i})\n"
                     for i in range(200)))
    ex = failure_excerpt(out)
    assert "Expected ';'" in ex and "\x1b" not in ex and "node_modules" not in ex
    long = "ERROR first\n" + "noise line\n" * 2000 + "last line\n"
    ex = failure_excerpt(long, limit=500)
    assert "ERROR first" in ex and "last line" in ex and len(ex) < 600


def test_failure_headline_names_the_failure_not_the_announcement():
    from ledger.executor import failure_headline
    vite = ("x Build failed in 82ms\n\x1b[31merror during build:\n"
            "src/scene.js (22:18): Expected ';'\n")
    assert failure_headline(vite) == \
        "error during build: src/scene.js (22:18): Expected ';'"
    game = ("CHECK page load\nVERIFIER_ERROR\n"
            "page.evaluate: TypeError: x is undefined\n    at f (a.js:1)\n")
    assert failure_headline(game) == "page.evaluate: TypeError: x is undefined"
    pytest_out = ("..F\nFAILED tests/test_calc.py::test_add - assert 1 == 2\n"
                  "1 failed, 2 passed\n")
    assert failure_headline(pytest_out).startswith("FAILED tests/test_calc.py")


@needs_git
def test_the_author_sees_the_run_history(repo, tmp_path):
    """43 of 208 chess runs regressed to an earlier check; the author only
    ever saw the latest failure, so it could not know."""
    from ledger.executor import RunSuiteSkill
    ctx = _ctx(repo, tmp_path)
    ctx.history_path = tmp_path / "suite_history.jsonl"
    RunSuiteSkill(TEST_CMD).run(ctx)          # the fixture repo is red
    ctx.record_suite(True, "all checks passed")
    prompts: list[str] = []
    AuthorPatchSkill("a", FakeBackend(lambda p: prompts.append(p) or FIX_DIFF),
                     "fix add()").run(ctx)
    assert "TEST RUNS SO FAR" in prompts[0]
    assert "1. FAIL:" in prompts[0] and "2. PASS: all checks passed" in prompts[0]


def _pkg(root, name, entry_text, meta):
    pkg = root / "node_modules" / name
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps(meta), encoding="utf-8")
    (pkg / "esm.js").write_text(entry_text, encoding="utf-8")


def test_import_errors_default_from_a_package_without_one(tmp_path):
    """Sixteen chess-loop rounds died on `import Chess from 'chess.js'`."""
    _pkg(tmp_path, "chess.js", "class Chess {}\nexport { Chess, WHITE };\n",
         {"main": "cjs.js", "module": "esm.js"})
    bad = [("src/game.js", "import Chess from 'chess.js';\n")]
    errs = import_errors(tmp_path, bad)
    assert len(errs) == 1 and "no default export" in errs[0] and "Chess" in errs[0]
    good = [("src/game.js", "import { Chess } from 'chess.js';\n")]
    assert import_errors(tmp_path, good) == []


def test_import_errors_names_a_sibling_never_exported(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "scene.js").write_text(
        "export function initScene() {}\nexport const a = 1, b = 2;\n",
        encoding="utf-8")
    errs = import_errors(tmp_path, [(
        "src/main.js",
        "import { initScene, Scene3D } from './scene.js';\n"
        "import { thing } from './missing.js';\n")])
    assert any("Scene3D" in e and "initScene" in e for e in errs)
    assert any("missing.js" in e and "does not exist" in e for e in errs)
    # a file written in the same patch counts as its new version
    errs = import_errors(tmp_path, [
        ("src/main.js", "import { Scene3D as S } from './scene';\n"),
        ("src/scene.js", "export class Scene3D {}\n")])
    assert errs == []


def test_import_errors_stays_quiet_when_it_cannot_know(tmp_path):
    """A guess would be a false alarm: star re-exports, CommonJS packages,
    subpath imports and uninstalled dependencies are never flagged."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "all.js").write_text("export * from './x.js';\n",
                                             encoding="utf-8")
    files = [("src/m.js", "import { anything } from './all.js';\n"
                          "import three from 'three/addons/x.js';\n"
                          "import cjs from 'left-pad';\n")]
    assert import_errors(tmp_path, files) == []          # no node_modules
    _pkg(tmp_path, "left-pad", "module.exports = 1;\n", {"main": "esm.js"})
    assert import_errors(tmp_path, files) == []          # CommonJS
