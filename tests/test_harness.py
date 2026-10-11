"""The HARNESS_STUDY.md build order: failure signatures, the verify-before-
author contract, import evidence and the apply gate, content-located hunks,
process containment, per-action snapshots, MCP argument templating."""

import json
import subprocess
import sys
import time

import pytest
from conftest import BUGGY, FIXED, GOAL, TEST_CMD, needs_git

from ledger import sandbox
from ledger.actions import Action
from ledger.cli import main
from ledger.executor import (ApplyPatchSkill, BlobStore, ExecContext,
                             run_repo_episode)
from ledger.failures import (STUCK_AT, STUCK_PROP, FailureTracker,
                             broken_imports, failing_names, failure_signature,
                             symbol_evidence)
from ledger.hunks import LocateError, apply_by_content
from ledger.llm import FakeBackend
from ledger.mcp import expand
from ledger.sensors import ExpensiveResultCache
from ledger.skills_llm import AuthorPatchSkill, build_authoring_catalog
from ledger.snapshots import Snapshots


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=True).stdout


def _commit(root, msg="init"):
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "-m", msg)


def _ctx(worktree, tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    return ExecContext(worktree=worktree, blobs=BlobStore(tmp_path / "blobs"),
                       test_cache=ExpensiveResultCache(), queue_dir=queue,
                       applied_dir=tmp_path / "applied")


RELAY_ERR = """\
==================================== ERRORS ====================================
____________________ ERROR collecting tests/test_routes.py _____________________
ImportError while importing test module '{root}/tests/test_routes.py'.
tests/test_routes.py:1: in <module>
    from server.routes import list_workspaces
server/routes.py:{line}: in <module>
    from server.deps import get_hub
E   ImportError: cannot import name 'get_hub' from 'server.deps' ({root}/server/deps.py)
=========================== short test summary info ============================
ERROR tests/test_routes.py
1 error in {secs}s
"""


# -- negative preconditions ---------------------------------------------------

def test_forbid_blocks_applicability_and_keeps_old_hashes_stable():
    a = Action("A", prec=frozenset({"x"}), forbid=frozenset({"y"}))
    assert a.applicable(frozenset({"x"}))
    assert not a.applicable(frozenset({"x", "y"}))
    assert Action.from_dict(a.to_dict()) == a
    assert "forbid" not in Action("B").to_dict()


def test_author_needs_a_verified_tree_and_an_unstuck_failure():
    author = build_authoring_catalog(["a"]).get("AuthorPatch:a")
    base = frozenset({"budget.llm:ok", "tests.fail_count:unit:1-5"})
    assert author.applicable(base)
    assert not author.applicable(base | {"tests.unknown:unit"})
    assert not author.applicable(base | {STUCK_PROP})


# -- failure signatures -------------------------------------------------------

def test_signature_ignores_lines_paths_and_durations():
    a = failure_signature(RELAY_ERR.format(root="C:/w/one", line=11, secs="0.41"))
    b = failure_signature(RELAY_ERR.format(root="/tmp/two", line=14, secs="1.93"))
    assert a.sig == b.sig
    assert a.phase == "collect"
    assert "get_hub" in a.summary


def test_signature_changes_when_the_failure_does():
    a = failure_signature(RELAY_ERR.format(root="/w", line=11, secs="0.4"))
    b = failure_signature("FAILED tests/test_x.py::test_add - assert 1 == 5\n")
    assert a.sig != b.sig and b.phase == "test"


def test_tracker_counts_resets_and_persists(tmp_path):
    t = FailureTracker()
    err = RELAY_ERR.format(root="/w", line=11, secs="0.4")
    for i in range(1, STUCK_AT + 1):
        t.observe(False, err)
        t.applied(f"diff {i}")
        assert t.repeat == i
    assert t.stuck
    assert "OCCURRED 5 TIMES" in t.prompt_block()
    assert len(t.attempts) == 3                     # only the most recent
    t.save(tmp_path / "f.json")
    assert FailureTracker.load(tmp_path / "f.json").repeat == STUCK_AT
    t.observe(False, "FAILED t.py::other - boom")
    assert t.repeat == 1 and not t.stuck and t.attempts == []
    t.observe(True, "")
    assert t.repeat == 0 and t.prompt_block() == ""


def test_failing_names():
    assert failing_names(RELAY_ERR) == ["get_hub"]
    assert failing_names("NameError: name 'foo' is not defined\n"
                         "ModuleNotFoundError: No module named 'pkg.mod'") == \
        ["foo", "mod"]


# -- relay fixture: a rename that left an import behind -----------------------

@pytest.fixture
def relay(tmp_path):
    root = tmp_path / "relay"
    (root / "server").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "server" / "__init__.py").write_text("", encoding="utf-8")
    (root / "server" / "deps.py").write_text(
        "def get_hub():\n    return 'hub'\n", encoding="utf-8")
    (root / "server" / "routes.py").write_text(
        "from server.deps import get_hub\n\n\ndef list_workspaces():\n"
        "    return [get_hub()]\n", encoding="utf-8")
    (root / "tests" / "test_routes.py").write_text(
        "from server.routes import list_workspaces\n\n\ndef test_list():\n"
        "    assert list_workspaces() == ['hub']\n", encoding="utf-8")
    _git(root, "init", "-q")
    _commit(root)
    # The earlier patch: renamed the provider, missed the importer.
    (root / "server" / "deps.py").write_text(
        "def get_events():\n    return 'hub'\n", encoding="utf-8")
    _commit(root, "rename get_hub to get_events")
    return root


@needs_git
def test_broken_imports_finds_the_stale_importer(relay):
    found = broken_imports(relay)
    assert [(b.importer, b.line, b.module, b.name) for b in found] == \
        [("server/routes.py", 1, "server.deps", "get_hub")]


@needs_git
def test_broken_imports_judges_only_the_repository(tmp_path):
    root = tmp_path / "r"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("from .a import *\n", encoding="utf-8")
    (root / "pkg" / "a.py").write_text("X = 1\n", encoding="utf-8")
    (root / "pkg" / "sub.py").write_text(
        "import json\nfrom os import nope\nfrom . import a\nfrom .a import X\n"
        "from pkg import anything\n", encoding="utf-8")
    _git(root, "init", "-q")
    assert broken_imports(root) == []      # external, submodule, star-module


@needs_git
def test_symbol_evidence_points_at_the_rename(relay):
    block, paths = symbol_evidence(relay, RELAY_ERR)
    assert "server/routes.py:1:from server.deps import get_hub" in block
    assert "rename get_hub to get_events" in block      # git log -S
    assert "server/routes.py" in paths


# -- the apply gate -------------------------------------------------------------

@needs_git
def test_apply_gate_reverts_a_patch_that_breaks_an_import(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    (ctx.queue_dir / "a.patch").write_text(
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n-def add(a, b):\n"
        "+def plus(a, b):\n     return a - b\n", encoding="utf-8")
    result = ApplyPatchSkill("a").run(ctx)
    assert not result.ok
    assert "test_calc.py:1 imports `add`" in result.claims[0]
    assert (repo / "calc.py").read_text(encoding="utf-8") == BUGGY
    assert not (ctx.queue_dir / "a.patch").exists()
    assert "REVERTED BEFORE TESTING" in ctx.apply_evidence


@needs_git
def test_apply_gate_reverts_a_syntax_error_and_passes_a_good_patch(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    (ctx.queue_dir / "a.patch").write_text(
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n"
        "-    return a - b\n+    return a +\n", encoding="utf-8")
    assert not ApplyPatchSkill("a").run(ctx).ok
    assert "syntax error" in ctx.apply_evidence
    (ctx.queue_dir / "b.patch").write_text(
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n"
        "-    return a - b\n+    return a + b\n", encoding="utf-8")
    assert ApplyPatchSkill("b").run(ctx).ok
    assert (repo / "calc.py").read_text(encoding="utf-8") == FIXED
    assert ctx.apply_evidence == ""


# -- content-located hunks ------------------------------------------------------

SOURCE = ("import os\n\n\nclass Calc:\n    def add(self, a, b):\n"
          "        return a - b\n\n    def sub(self, a, b):\n"
          "        return a - b\n")


def test_hunk_with_wrong_header_and_indentation_is_located(tmp_path):
    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    diff = ("--- a/m.py\n+++ b/m.py\n@@ -40,3 +40,3 @@\n"
            " def add(self, a, b):\n-    return a - b\n+    return a + b\n")
    files, tiers = apply_by_content(tmp_path, diff)
    assert tiers == ["strip"]
    assert files == [("m.py", SOURCE.replace("a - b\n\n", "a + b\n\n", 1))]


def test_block_anchor_tolerates_a_drifted_middle(tmp_path):
    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    diff = ("--- a/m.py\n+++ b/m.py\n@@ -1,6 +1,6 @@\n"
            " class Calc:\n     def add(self, x, y):\n-        return a - b\n"
            "+        return a + b\n     def sub(self, a, b):\n")
    files, tiers = apply_by_content(tmp_path, diff)
    assert tiers == ["anchor"]
    # The change lands; the drifted context line stays as the file had it.
    assert files == [("m.py", SOURCE.replace("a - b\n\n", "a + b\n\n", 1))]


def test_unlocatable_hunk_names_the_nearest_lines(tmp_path):
    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    diff = ("--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n"
            "-    def multiply(self, a, b):\n+    def mul(self, a, b):\n")
    with pytest.raises(LocateError, match="nearest lines"):
        apply_by_content(tmp_path, diff)


@needs_git
def test_author_queues_a_diff_git_rejected_once_located(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    sloppy = ("--- a/calc.py\n+++ b/calc.py\n@@ -7,2 +7,2 @@\n"
              " def add(a, b):  \n-  return a - b\n+  return a + b\n")
    result = AuthorPatchSkill("a", FakeBackend(sloppy), "fix add").run(ctx)
    assert result.ok, result.claims
    assert any("located the hunks by content" in c for c in result.claims)
    assert ApplyPatchSkill("a").run(ctx).ok
    assert (repo / "calc.py").read_text(encoding="utf-8") == FIXED


@needs_git
def test_small_diff_cap_applies_only_when_some_tests_still_pass(repo, tmp_path):
    """The cap protects lines that exist while other tests pass. A stub is
    free to grow (insertions break nothing, hardbench crc); a working
    60-line module replaced wholesale under passing tests is refused."""
    ctx = _ctx(repo, tmp_path)
    (repo / "calc.py").write_text("def add(a, b):\n    raise NotImplementedError\n",
                                  encoding="utf-8")
    body = "".join(f"# step {i}\n" for i in range(60)) + "\n\ndef add(a, b):\n    return a + b\n"
    response = "FILE: calc.py\n```python\n" + body + "```\n"
    ctx.suite_evidence["unit"] = {"tail": "FAILED tests/test_calc.py::test_add\n"
                                          "1 failed in 0.10s", "sha": "x"}
    result = AuthorPatchSkill("a", FakeBackend(response), "implement add").run(ctx)
    assert result.ok, result.claims
    ctx.suite_evidence["unit"] = {"tail": "FAILED tests/test_calc.py::test_add\n"
                                          "1 failed, 3 passed in 0.10s", "sha": "x"}
    result = AuthorPatchSkill("b", FakeBackend(response), "implement add").run(ctx)
    assert result.ok, result.claims
    (repo / "calc.py").write_text(
        "".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(30)), encoding="utf-8")
    result = AuthorPatchSkill("c", FakeBackend(response), "implement add").run(ctx)
    assert not result.ok
    assert any("exceeds the" in c for c in result.claims), result.claims


# -- the relay episode: stuck is detected, not paid for -------------------------

@needs_git
def test_a_repeated_failure_escalates_stuck_before_the_budget_runs_out(relay, tmp_path):
    prompts = []

    def responder(prompt):
        if "REPOSITORY FILES" not in prompt:
            return json.dumps({"goal": ["tests.pass:unit", "repo.clean"],
                               "rationale": "green"})
        prompts.append(prompt)
        n = len(prompts)
        # A different patch every time, none of which touches the real defect.
        return ("FILE: server/deps.py\n```python\n"
                f"def get_events():\n    return 'hub'  # attempt {n}\n```\n")

    episode = tmp_path / "ep"
    result, executor, journal_path = run_repo_episode(
        relay, episode, None, TEST_CMD, task_text="make the suite pass",
        backend=FakeBackend(responder), slots=["a"], max_author_attempts=12)

    assert not result.success
    entries = [json.loads(ln) for ln in journal_path.read_text(
        encoding="utf-8").splitlines()]
    esc = [e for e in entries if e["t"] == "ESCALATION"][-1]
    assert esc["reason"] == "stuck"
    assert esc["repeat"] == STUCK_AT
    assert esc["author_attempts"] < 12
    # Never authored on top of an untested patch.
    runs = [a for a in result.plan if a in ("AuthorPatch:a", "RunSuite")]
    assert "AuthorPatch:a" not in [b for a, b in zip(runs, runs[1:]) if a == b]
    # The later prompts carried the stuck block and the import evidence.
    assert "THE SAME FAILURE HAS NOW OCCURRED" in prompts[-1]
    assert "server/routes.py:1:from server.deps import get_hub" in prompts[-1]
    assert "REPO-LOCAL IMPORTS THAT DO NOT RESOLVE" in prompts[-1]
    # Every worktree-changing step recorded the tree it started from.
    trees = [e for e in entries if e["t"] == "EXEC" and e["action"] == "ApplyPatch:a"]
    assert trees and all(len(e["tree"]) == 40 for e in trees)


@needs_git
def test_rewind_restores_the_tree_before_a_step(repo, tmp_path, capsys):
    def responder(prompt):
        if "REPOSITORY FILES" in prompt:
            return ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n"
                    "-    return a - b\n+    return a + b\n")
        return json.dumps({"goal": sorted(GOAL), "rationale": "green"})

    episode = tmp_path / "ep"
    result, _, _ = run_repo_episode(repo, episode, None, TEST_CMD,
                                    task_text="fix it", backend=FakeBackend(responder),
                                    slots=["a"])
    assert result.success, result.plan
    worktree = episode / "worktree"
    assert (worktree / "calc.py").read_text(encoding="utf-8") == FIXED
    assert main(["rewind", str(episode)]) == 0
    assert "before ApplyPatch:a" in capsys.readouterr().out
    assert main(["rewind", str(episode), "--step", "1"]) == 0
    assert (worktree / "calc.py").read_text(encoding="utf-8") == BUGGY


# -- snapshots ------------------------------------------------------------------

@needs_git
def test_snapshot_restore_round_trip(repo, tmp_path):
    snaps = Snapshots(tmp_path / "snap.git", repo)
    tree = snaps.take()
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    (repo / "new.txt").write_text("x", encoding="utf-8")
    backup = snaps.restore(tree)
    assert backup != tree
    assert (repo / "calc.py").read_text(encoding="utf-8") == BUGGY
    assert not (repo / "new.txt").exists()
    assert _git(repo, "status", "--porcelain") == ""   # user's index untouched
    snaps.restore(backup)
    assert (repo / "new.txt").read_text(encoding="utf-8") == "x"


# -- containment ----------------------------------------------------------------

def test_sandbox_kills_what_the_command_left_running():
    child = ("import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', "
             "'import time; time.sleep(60)']); print(p.pid, flush=True)")
    start = time.monotonic()
    proc = sandbox.run([sys.executable, "-c", child], text=True, timeout=30)
    assert proc.returncode == 0
    assert time.monotonic() - start < 20          # did not wait for the sleeper
    pid = int(proc.stdout.split()[0])
    time.sleep(0.5)
    if sys.platform == "win32":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                 capture_output=True, text=True).stdout
        assert str(pid) not in listing
    else:
        import os
        with pytest.raises(OSError):
            os.kill(pid, 0)


def test_sandbox_timeout_raises_like_subprocess_run():
    with pytest.raises(subprocess.TimeoutExpired):
        sandbox.run([sys.executable, "-c", "import time; time.sleep(30)"],
                    text=True, timeout=1)


# -- MCP argument templating ------------------------------------------------------

def test_mcp_arguments_expand_episode_variables_only():
    got = expand({"ref": "${branch}", "paths": ["${worktree}/x", "${HOME}"], "n": 3},
                 {"branch": "ledger/ep", "worktree": "/w"})
    assert got == {"ref": "ledger/ep", "paths": ["/w/x", "${HOME}"], "n": 3}


SLOW_STUB = r'''
import json, sys, time
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": "slow"}]}})
    elif method == "tools/call":
        if msg["params"]["name"] == "slow":
            time.sleep(3)
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": "done"}]}})
'''


def test_mcp_call_timeout_cancels_without_killing_the_server(tmp_path):
    from ledger.mcp import MCPServer
    stub = tmp_path / "slow.py"
    stub.write_text(SLOW_STUB, encoding="utf-8")
    srv = MCPServer("slow", [sys.executable, str(stub)], call_timeout=1)
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            srv.call("slow", {})
        srv._call_timeout = 10
        assert srv.call("fast", {}) == "done"     # same process, still alive
    finally:
        srv.close()


@needs_git
def test_best_of_n_prefers_the_agreeing_majority(repo, tmp_path):
    from types import SimpleNamespace
    from ledger.vote import rank
    base = _git(repo, "rev-parse", "HEAD").strip()
    bodies = {"x": "def add(a, b):\n    return a+b\n",
              "y": "def add(a, b):\n    # fixed\n    return a + b\n",
              "z": "def add(a, b):\n    return b + a\n"}
    outcomes = []
    for name, body in bodies.items():
        ep = tmp_path / name
        _git(repo, "worktree", "add", "-q", "--detach", str(ep / "worktree"))
        (ep / "worktree" / "calc.py").write_text(body, encoding="utf-8")
        outcomes.append(SimpleNamespace(name=name, success=True,
                                        steps=3 if name == "z" else 5,
                                        journal_path=ep / "episode.jsonl"))
    outcomes.append(SimpleNamespace(name="w", success=False, steps=1,
                                    journal_path=None))
    assert [o.name for o in rank(outcomes, base)] == ["x", "y", "z", "w"]
