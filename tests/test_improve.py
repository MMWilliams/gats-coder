"""`ledger improve`: the self-audit and the fix-verify-commit loop, on a toy
package named `ledger` so the audit runs against it and not this checkout."""
import difflib
import json
import subprocess

import pytest
from conftest import needs_git

from ledger.improve import (BRANCH, Finding, Outcome, audit, changes, improve,
                            policy)

GOOD = "def f():\n    return 1\n"
BAD = "def g():\n    return missing_name\n"
TEST = ("from ledger.good import f\n\n\ndef test_f():\n    assert f() == 1\n\n\n"
        "def test_broken():\n    assert f() == 2\n")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    (root / "ledger").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "ledger" / "__init__.py").write_text("", encoding="utf-8")
    (root / "ledger" / "good.py").write_text(GOOD, encoding="utf-8")
    (root / "ledger" / "bad.py").write_text(BAD, encoding="utf-8")
    (root / "ledger" / "lonely.py").write_text("X = 1\n", encoding="utf-8")
    (root / "tests" / "test_good.py").write_text(TEST, encoding="utf-8")
    (root / ".gitignore").write_text(".ledger/\n__pycache__/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "toy")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    return root


def _diff(path, old, new):
    body = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"a/{path}", f"b/{path}"))
    return f"diff --git a/{path} b/{path}\n{body}"


@needs_git
def test_audit_finds_every_kind_on_the_tree_it_was_given(toy):
    a = audit(toy, timeout=300)
    kinds = [(f.kind, f.component) for f in a.findings]
    assert ("test_failure", "tests/test_good.py") in kinds
    assert ("undefined", "ledger/bad.py") in kinds
    assert ("untested", "ledger/bad.py") in kinds
    assert ("untested", "ledger/lonely.py") in kinds
    assert ("untested", "ledger/good.py") not in kinds
    # ranked: a failing test before a lint bug before missing coverage
    order = [f.kind for f in a.findings]
    assert order.index("test_failure") < order.index("undefined") < order.index("untested")
    assert a.components["ledger/good.py"] == ["tests/test_good.py"]


@needs_git
def test_audit_reports_syntax_and_import_failures(toy):
    (toy / "ledger" / "lonely.py").write_text("def broken(:\n", encoding="utf-8")
    (toy / "ledger" / "good.py").write_text("import no_such_module\n" + GOOD,
                                            encoding="utf-8")
    kinds = {(f.kind, f.component) for f in audit(toy, timeout=300).findings}
    assert ("syntax", "ledger/lonely.py") in kinds
    assert ("import", "ledger/good.py") in kinds


def test_policy_allows_implementation_and_a_new_audit_test_only():
    bug = Finding("undefined", "ledger/bad.py", "x", "x")
    fix = _diff("ledger/bad.py", BAD, "def g():\n    return 0\n")
    assert policy(fix, bug) == ""
    assert "outside" in policy(_diff("tests/test_good.py", TEST, TEST + "\n"), bug)
    assert "outside" in policy(_diff("ledger/improve.py", "a\n", "b\n"), bug)
    assert policy("", bug) == "the episode changed nothing"
    lonely = Finding("untested", "ledger/lonely.py", "x", "x")
    new = "diff --git a/tests/test_audit_lonely.py b/tests/test_audit_lonely.py\n" \
          "new file mode 100644\n--- /dev/null\n+++ b/tests/test_audit_lonely.py\n" \
          "@@ -0,0 +1 @@\n+def test_x(): pass\n"
    assert changes(new) == [("A", "tests/test_audit_lonely.py")]
    assert policy(new, lonely) == ""
    assert "outside" in policy(new, bug)


@needs_git
def test_loop_commits_a_verified_fix_and_refuses_a_moved_bar(toy):
    def fake(f, n, worktree, state_dir):
        if f.kind == "test_failure":
            # "fixes" the failing test by rewriting it: must be refused
            return Outcome(diff=_diff("tests/test_good.py", TEST,
                                      TEST.replace("f() == 2", "f() == 1")))
        if f.kind == "undefined":
            return Outcome(diff=_diff("ledger/bad.py", BAD, "def g():\n    return 0\n"))
        return Outcome(verdict="nothing to say about " + f.component)

    out = []
    result = improve(toy, max_iterations=3, test_timeout=300, episode=fake,
                     say=out.append)
    wt = toy / ".ledger" / "improve" / "worktree"
    log = [json.loads(l) for l in (toy / ".ledger" / "improve" / "log.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    assert [(r["finding"]["kind"], r["result"]) for r in log] == [
        ("test_failure", "rejected"), ("undefined", "accepted"), ("untested", "rejected")]
    assert "outside what a self-improvement may change" in log[0]["reason"]
    assert log[2]["verdict"] == "nothing to say about ledger/bad.py"
    # the fix is a commit on the improve branch; the operator's tree is untouched
    assert (wt / "ledger" / "bad.py").read_text(encoding="utf-8") == "def g():\n    return 0\n"
    assert (toy / "ledger" / "bad.py").read_text(encoding="utf-8") == BAD
    head = subprocess.run(["git", "log", "--format=%s", "-1", BRANCH], cwd=toy,
                          capture_output=True, text=True).stdout
    assert head.startswith("improve(ledger/bad.py): undefined")
    assert result["iterations"] == 3


@needs_git
def test_a_fix_that_breaks_something_else_is_rolled_back(toy):
    def fake(f, n, worktree, state_dir):
        return Outcome(diff=_diff("ledger/bad.py", BAD, "def g(:\n"))

    improve(toy, max_iterations=2, test_timeout=300, say=lambda s: None,
            episode=lambda f, n, wt, sd: fake(f, n, wt, sd) if f.kind == "undefined"
            else Outcome())
    log = [json.loads(l) for l in (toy / ".ledger" / "improve" / "log.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    undefined = next(r for r in log if r["finding"]["kind"] == "undefined")
    assert undefined["result"] == "rejected"
    assert "introduced" in undefined["reason"] and "syntax" in undefined["reason"]
    wt = toy / ".ledger" / "improve" / "worktree"
    assert (wt / "ledger" / "bad.py").read_text(encoding="utf-8") == BAD


def test_the_grading_command_survives_ledgers_whitespace_split(tmp_path):
    """`ledger run --test` is split on whitespace and run without a shell.
    The first improve run quoted the interpreter path, every episode died
    with WinError 2 on its first RunSuite, and nothing was authored."""
    import shutil
    import sys

    from ledger.improve import _task
    _, test = _task(Finding("undefined", "ledger/bad.py", "x", "x"))
    argv = test.split()
    assert '"' not in test
    assert shutil.which(argv[0]) or argv[0] == sys.executable
    assert argv[-2:] == ["--check", "ledger/bad.py"]


def test_claude_may_not_solve_ledgers_own_problems(toy):
    from ledger.improve import independent_backend
    assert independent_backend(["--backend", "ollama", "--model", "qwen3.8:27b"]) == ""
    assert independent_backend(["--backend", "claude"])
    assert independent_backend(["--backend", "anthropic", "--model", "x"])
    assert independent_backend([])   # the config default may be Claude
    with pytest.raises(ValueError):
        improve(toy, backend_args=["--backend", "claude"], say=lambda s: None)


@needs_git
def test_a_fix_authored_by_claude_is_refused_even_if_correct(toy):
    good = _diff("ledger/bad.py", BAD, "def g():\n    return 0\n")
    improve(toy, max_iterations=2, test_timeout=300, say=lambda s: None,
            episode=lambda f, n, wt, sd: Outcome(diff=good, models=("claude-cli:default",))
            if f.kind == "undefined" else Outcome())
    log = [json.loads(l) for l in (toy / ".ledger" / "improve" / "log.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    undefined = next(r for r in log if r["finding"]["kind"] == "undefined")
    assert undefined["result"] == "rejected"
    assert "not independent" in undefined["reason"]


@needs_git
def test_a_finding_is_tried_twice_then_left(toy):
    improve(toy, max_iterations=20, test_timeout=300, say=lambda s: None,
            episode=lambda f, n, wt, sd: Outcome())
    state = json.loads((toy / ".ledger" / "improve" / "state.json").read_text())
    assert state["findings"] and all(v["tries"] == 2 for v in state["findings"].values())
    assert state["iterations"] == 2 * len(state["findings"])
