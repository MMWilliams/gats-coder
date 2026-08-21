"""Launching what was built (design SS13)."""

import json
import types

import pytest

from ledger import launch as launch_mod
from ledger.cli import _launch_if_asked


def test_a_static_site_is_served_not_opened_from_disk(tmp_path):
    """index.html must be served over http, never handed to the browser as
    a file:// path: ES modules are blocked on that origin, so the page comes
    up blank and looks exactly like the build failed."""
    (tmp_path / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    plan = launch_mod.detect(tmp_path)
    assert plan is not None and plan.kind == "static"
    assert plan.url.startswith("http://127.0.0.1:")
    assert "http.server" in " ".join(plan.command)


def test_a_declared_npm_script_beats_a_guess(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8")
    monkeypatch.setattr(launch_mod.shutil, "which", lambda _n: "/usr/bin/npm")
    plan = launch_mod.detect(tmp_path)
    assert plan.kind == "npm" and plan.command == ["npm", "run", "dev"]


def test_nothing_to_launch_is_not_an_error(tmp_path):
    assert launch_mod.detect(tmp_path) is None


def test_a_failed_run_is_never_launched(tmp_path, monkeypatch):
    """The gate is success. Starting a build already known to be broken
    spends the human's attention on something they were just told does not
    work, and buries the escalation under a browser window."""
    called = []
    monkeypatch.setattr(launch_mod, "launch",
                        lambda *a, **k: called.append(a) or None)
    # _launch_if_asked is only ever reached on the success branch; assert
    # that branch is where it lives, so a refactor cannot quietly move it.
    import inspect

    from ledger import cli
    src = inspect.getsource(cli.cmd_run)
    before, _, after = src.partition("_launch_if_asked")
    assert "if result.success:" in before.split("print(ui.headline")[-1], (
        "the launch call is no longer inside the success branch")
    assert after, "sanity: the call should exist"


def test_no_launch_flag_beats_config(tmp_path):
    args = types.SimpleNamespace(launch=False)
    assert _launch_if_asked(args, {"launch": True}, tmp_path) is None


def test_silent_unless_asked(tmp_path):
    args = types.SimpleNamespace(launch=None)
    assert _launch_if_asked(args, {}, tmp_path) is None


def test_config_can_turn_it_on(tmp_path, monkeypatch):
    seen = {}

    def fake(root, command=None, **kw):
        seen["root"], seen["command"] = root, command
        return launch_mod.Launch("static", None, "http://x", tmp_path, "note")

    monkeypatch.setattr("ledger.launch.launch", fake)
    args = types.SimpleNamespace(launch=None)
    got = _launch_if_asked(args, {"launch": True}, tmp_path)
    assert got is not None and seen["command"] is None


def test_an_explicit_command_wins_over_detection(tmp_path, monkeypatch):
    seen = {}

    def fake(root, command=None, **kw):
        seen["command"] = command
        return launch_mod.Launch("explicit", None, None, tmp_path, command)

    monkeypatch.setattr("ledger.launch.launch", fake)
    args = types.SimpleNamespace(launch="npm run preview")
    got = _launch_if_asked(args, {"launch": True}, tmp_path)
    assert seen["command"] == "npm run preview"
    assert got.note == "npm run preview"


def test_global_config_layers_under_the_repo(tmp_path, monkeypatch):
    """`~/.ledger/config.json` carries what holds everywhere; the repo file
    overrides it. Without this there is no way to say "launch anything you
    build" once -- it would have to be repeated in every project."""
    from ledger.cli import _config

    home = tmp_path / "home"
    (home / ".ledger").mkdir(parents=True)
    (home / ".ledger" / "config.json").write_text(
        json.dumps({"launch": True, "profile": "attended",
                    "backend": {"id": "claude", "timeout": 900}}),
        encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / ".ledger").mkdir(parents=True)
    (repo / ".ledger" / "config.json").write_text(
        json.dumps({"profile": "autonomous", "backend": {"timeout": 60}}),
        encoding="utf-8")

    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: home))
    cfg = _config(repo)
    assert cfg["launch"] is True, "the global preference was lost"
    assert cfg["profile"] == "autonomous", "the repo must win"
    # nested blocks merge key by key, so a repo can override one field
    assert cfg["backend"] == {"id": "claude", "timeout": 60}


def test_no_config_anywhere_is_still_empty(tmp_path, monkeypatch):
    from ledger.cli import _config
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))
    assert _config(tmp_path / "nowhere") == {}


def test_authoring_gets_its_own_longer_deadline():
    """Writing a whole application is not the same call as compiling a
    one-line goal contract. One flat timeout served neither: on a three.js
    game the CLI hit 600s eight times running and the episode spent its
    whole budget without a line of code being written."""
    from ledger.llm import AUTHOR_TIMEOUT, FakeBackend, with_timeout

    base = FakeBackend("hi")
    base._timeout = 600
    longer = with_timeout(base, AUTHOR_TIMEOUT)
    assert AUTHOR_TIMEOUT >= 1800
    assert longer._timeout == AUTHOR_TIMEOUT
    # a COPY: the same backend object is shared with the goal compiler and
    # the planner's L3 layer, and a hung backend must not stall those for
    # half an hour too
    assert base._timeout == 600
    assert longer is not base


def test_a_backend_without_a_timeout_is_passed_through():
    from ledger.llm import with_timeout
    assert with_timeout(None, 1800) is None
    sentinel = object()
    assert with_timeout(sentinel, 1800) is sentinel


def test_a_run_where_every_attempt_timed_out_is_reported_as_such(tmp_path):
    """'budget exhausted' after every attempt died on the clock is a
    deadline problem wearing a budget problem's clothes -- doubling the
    attempts just buys more timeouts."""
    from ledger.cli import _all_attempts_timed_out
    from ledger.journal import Journal

    p = tmp_path / "e.jsonl"
    with Journal(str(p)) as j:
        j.append("HEADER", version="t", task_id="t", initial=[], goal=[],
                 catalog=[], catalog_hash="x", config={})
        j.append("EXEC", action="AuthorPatch:a", ok=False,
                 claims=["attempt 1: backend error: claude CLI timed out "
                         "after 600s"])
    assert _all_attempts_timed_out(p) is True

    q = tmp_path / "f.jsonl"
    with Journal(str(q)) as j:
        j.append("HEADER", version="t", task_id="t", initial=[], goal=[],
                 catalog=[], catalog_hash="x", config={})
        j.append("EXEC", action="AuthorPatch:a", ok=False,
                 claims=["attempt 1: diff did not apply"])
    assert _all_attempts_timed_out(q) is False, (
        "a real failure must not be mislabelled as a deadline problem")

    empty = tmp_path / "g.jsonl"
    with Journal(str(empty)) as j:
        j.append("HEADER", version="t", task_id="t", initial=[], goal=[],
                 catalog=[], catalog_hash="x", config={})
    assert _all_attempts_timed_out(empty) is False
