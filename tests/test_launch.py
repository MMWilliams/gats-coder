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
