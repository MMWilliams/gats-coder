"""Pytest cases for ledger/wizard.py public behaviour.

No network, no model calls, nothing written outside tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ledger import wizard


# ─── helpers ───────────────────────────────────────────────────────────────────

def _dep(present=None, *, label="TestTool", why="needed for testing",
         install_cmd=None, manual=None, key="test"):
    """Build a fake dependency object matching the interface _offer expects."""
    d = MagicMock()
    d.present.return_value = present
    d.label = label
    d.why = why
    d.install_command.return_value = install_cmd
    d.manual = manual
    d.key = key
    return d


def _echo():
    """Return an echo function that collects output lines."""
    lines: list[str] = []
    def echo(msg=""):
        lines.append(msg)
    echo.lines = lines  # type: ignore[attr-defined]
    return echo


# ─── _write_global ─────────────────────────────────────────────────────────────

class TestWriteGlobal:
    def test_creates_new_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        path = wizard._write_global({"backend": {"id": "claude"}})
        assert path == tmp_path / ".ledger" / "config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"backend": {"id": "claude"}}

    def test_merges_nested_dict_preserving_existing_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg = tmp_path / ".ledger" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps(
            {"backend": {"id": "claude", "model": "x"}, "launch": True}
        ), encoding="utf-8")
        wizard._write_global({"backend": {"model": "y"}})
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data["backend"] == {"id": "claude", "model": "y"}
        assert data["launch"] is True

    def test_overwrites_non_dict_with_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg = tmp_path / ".ledger" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"backend": "old-string"}), encoding="utf-8")
        wizard._write_global({"backend": {"id": "claude"}})
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data["backend"] == {"id": "claude"}

    def test_corrupt_existing_file_recovers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg = tmp_path / ".ledger" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("not valid json {{{", encoding="utf-8")
        wizard._write_global({"launch": True})
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data == {"launch": True}

    def test_non_dict_existing_value_recovers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg = tmp_path / ".ledger" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("[1, 2, 3]", encoding="utf-8")
        wizard._write_global({"launch": True})
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data == {"launch": True}

    def test_overwrites_dict_with_scalar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg = tmp_path / ".ledger" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"backend": {"id": "claude"}}), encoding="utf-8")
        wizard._write_global({"backend": "override"})
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data["backend"] == "override"


# ─── _ask / _yes ───────────────────────────────────────────────────────────────

class TestAsk:
    def test_returns_stripped_input(self):
        with patch("builtins.input", return_value="  hello  "):
            assert wizard._ask("what?") == "hello"

    def test_returns_default_on_empty_input(self):
        with patch("builtins.input", return_value=""):
            assert wizard._ask("what?", "fallback") == "fallback"

    def test_returns_default_on_eof(self):
        with patch("builtins.input", side_effect=EOFError):
            assert wizard._ask("what?", "fallback") == "fallback"

    def test_default_empty_string(self):
        with patch("builtins.input", return_value=""):
            assert wizard._ask("what?") == ""


class TestYes:
    def test_y_returns_true(self):
        with patch("builtins.input", return_value="y"):
            assert wizard._yes("ok?", default=False) is True

    def test_n_returns_false(self):
        with patch("builtins.input", return_value="n"):
            assert wizard._yes("ok?", default=True) is False

    def test_empty_returns_default_true(self):
        with patch("builtins.input", return_value=""):
            assert wizard._yes("ok?", default=True) is True

    def test_empty_returns_default_false(self):
        with patch("builtins.input", return_value=""):
            assert wizard._yes("ok?", default=False) is False

    def test_eof_returns_default(self):
        with patch("builtins.input", side_effect=EOFError):
            assert wizard._yes("ok?", default=True) is True
        with patch("builtins.input", side_effect=EOFError):
            assert wizard._yes("ok?", default=False) is False

    def test_yes_word(self):
        with patch("builtins.input", return_value="yes"):
            assert wizard._yes("ok?", default=False) is True

    def test_no_word(self):
        with patch("builtins.input", return_value="no"):
            assert wizard._yes("ok?", default=True) is False


# ─── _choose_backend ───────────────────────────────────────────────────────────

class TestChooseBackend:
    def test_claude_present(self):
        echo = _echo()
        dep = _dep(present="/usr/bin/claude", key="claude")
        with patch.object(wizard.deps, "backends", return_value=[dep]), \
             patch("builtins.input", return_value="1"):
            result = wizard._choose_backend(echo)
        assert result == {"backend": {"id": "claude"}}

    def test_claude_not_present_install_succeeds(self):
        echo = _echo()
        dep = _dep(present=None, key="claude",
                   install_cmd=["npm", "install", "-g", "claude"])
        # first present() call → None, second (re-detect) → path
        dep.present.side_effect = [None, "/usr/local/bin/claude"]
        with patch.object(wizard.deps, "backends", return_value=[dep]), \
             patch("builtins.input", side_effect=["1", "y"]), \
             patch.object(wizard.deps, "run_install", return_value=True):
            result = wizard._choose_backend(echo)
        assert result == {"backend": {"id": "claude"}}

    def test_claude_not_present_install_declined(self):
        echo = _echo()
        dep = _dep(present=None, key="claude", install_cmd=["brew", "install", "claude"])
        with patch.object(wizard.deps, "backends", return_value=[dep]), \
             patch("builtins.input", side_effect=["1", "n"]), \
             patch.object(wizard.deps, "run_install", return_value=True):
            result = wizard._choose_backend(echo)
        assert result is None

    def test_skip_choice_returns_none(self):
        echo = _echo()
        with patch("builtins.input", return_value="4"):
            result = wizard._choose_backend(echo)
        assert result is None

    def test_eof_defaults_to_choice_1(self):
        echo = _echo()
        dep = _dep(present="/usr/bin/claude", key="claude")
        with patch.object(wizard.deps, "backends", return_value=[dep]), \
             patch("builtins.input", side_effect=EOFError):
            result = wizard._choose_backend(echo)
        assert result == {"backend": {"id": "claude"}}

    def test_api_key_anthropic_env_set(self):
        echo = _echo()
        with patch("builtins.input", side_effect=["2", "1"]), \
             patch.object(wizard.credentials, "ENV_VAR", {"anthropic": "ANTHROPIC_API_KEY"}), \
             patch("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            result = wizard._choose_backend(echo)
        assert result is not None
        assert result["backend"]["id"] == "anthropic"
        assert result["backend"]["provider"] == "anthropic"

    def test_api_key_deepseek_gets_base_url(self):
        echo = _echo()
        with patch("builtins.input", side_effect=["2", "3"]), \
             patch.object(wizard.credentials, "ENV_VAR", {"deepseek": "DEEPSEEK_API_KEY"}), \
             patch("os.environ", {}), \
             patch("getpass.getpass", return_value="sk-deepseek"), \
             patch.object(wizard.credentials, "put", return_value=Path("/fake/creds")):
            result = wizard._choose_backend(echo)
        assert result is not None
        assert result["backend"]["id"] == "openai"
        assert result["backend"]["base_url"] == "https://api.deepseek.com/v1"
        assert result["backend"]["provider"] == "deepseek"

    def test_api_key_groq_gets_base_url(self):
        echo = _echo()
        with patch("builtins.input", side_effect=["2", "4"]), \
             patch.object(wizard.credentials, "ENV_VAR", {"groq": "GROQ_API_KEY"}), \
             patch("os.environ", {}), \
             patch("getpass.getpass", return_value="sk-groq"), \
             patch.object(wizard.credentials, "put", return_value=Path("/fake/creds")):
            result = wizard._choose_backend(echo)
        assert result is not None
        assert result["backend"]["id"] == "openai"
        assert result["backend"]["base_url"] == "https://api.groq.com/openai/v1"
        assert result["backend"]["provider"] == "groq"

    def test_api_key_empty_input_returns_none(self):
        echo = _echo()
        with patch("builtins.input", side_effect=["2", "1"]), \
             patch.object(wizard.credentials, "ENV_VAR", {"anthropic": "ANTHROPIC_API_KEY"}), \
             patch("os.environ", {}), \
             patch("getpass.getpass", return_value=""):
            result = wizard._choose_backend(echo)
        assert result is None

    def test_api_key_invalid_pick_falls_back_to_anthropic(self):
        echo = _echo()
        with patch("builtins.input", side_effect=["2", "99"]), \
             patch.object(wizard.credentials, "ENV_VAR", {"anthropic": "ANTHROPIC_API_KEY"}), \
             patch("os.environ", {}), \
             patch("getpass.getpass", return_value="sk-abc"), \
             patch.object(wizard.credentials, "put", return_value=Path("/fake/creds")):
            result = wizard._choose_backend(echo)
        assert result is not None
        assert result["backend"]["provider"] == "anthropic"

    def test_ollama_with_models(self):
        echo = _echo()
        dep = _dep(present="/usr/bin/ollama", key="ollama")
        with patch.object(wizard.deps, "backends", return_value=[dep]), \
             patch.object(wizard.deps, "ollama_models", return_value=["qwen3:30b", "llama3"]), \
             patch("builtins.input", side_effect=["3", "llama3"]):
            result = wizard._choose_backend(echo)
        assert result == {"backend": {"id": "ollama", "model": "llama3"}}

    def test_ollama_no_models_pull(self):
        echo = _echo()
        dep = _dep(present="/usr/bin/ollama", key="ollama")
        with patch.object(wizard.deps, "backends", return_value=[dep]), \
             patch.object(wizard.deps, "ollama_models", return_value=[]), \
             patch("builtins.input", side_effect=["3", "qwen3-coder:30b", "y"]), \
             patch.object(wizard.deps, "run_install", return_value=True):
            result = wizard._choose_backend(echo)
        assert result == {"backend": {"id": "ollama", "model": "qwen3-coder:30b"}}


# ─── run() integration ─────────────────────────────────────────────────────────

class TestRun:
    def test_return_zero_happy_path_claude(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        core = _dep(present="/usr/bin/python3")
        claude = _dep(present="/usr/bin/claude", key="claude")

        with patch.object(wizard.deps, "core", return_value=[core]), \
             patch.object(wizard.deps, "backends", return_value=[claude]), \
             patch.object(wizard.deps, "voice", return_value=[]), \
             patch.object(wizard.deps, "system", return_value="Linux"), \
             patch.object(wizard.deps, "package_manager", return_value="apt"), \
             patch("builtins.input", side_effect=["1", "n"]):
            ec = wizard.run(want_voice=False)

        assert ec == 0
        cfg = tmp_path / ".ledger" / "config.json"
        assert cfg.is_file()
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data["backend"]["id"] == "claude"
        # "launch" not set because user said "n"
        assert "launch" not in data

    def test_return_one_core_dep_uninstallable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        core = _dep(present=None, install_cmd=None, manual="https://example.com")
        claude = _dep(present="/usr/bin/claude", key="claude")

        with patch.object(wizard.deps, "core", return_value=[core]), \
             patch.object(wizard.deps, "backends", return_value=[claude]), \
             patch.object(wizard.deps, "voice", return_value=[]), \
             patch.object(wizard.deps, "system", return_value="Linux"), \
             patch.object(wizard.deps, "package_manager", return_value=None), \
             patch("builtins.input", side_effect=["1", "n"]):
            ec = wizard.run(want_voice=False)

        assert ec == 1

    def test_return_zero_core_dep_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        core = _dep(present=None, install_cmd=["apt", "install", "python3"])
        # re-detect succeeds after install
        core.present.side_effect = [None, "/usr/bin/python3"]
        claude = _dep(present="/usr/bin/claude", key="claude")

        with patch.object(wizard.deps, "core", return_value=[core]), \
             patch.object(wizard.deps, "backends", return_value=[claude]), \
             patch.object(wizard.deps, "voice", return_value=[]), \
             patch.object(wizard.deps, "system", return_value="Linux"), \
             patch.object(wizard.deps, "package_manager", return_value="apt"), \
             patch("builtins.input", side_effect=["y", "1", "n"]), \
             patch.object(wizard.deps, "run_install", return_value=True):
            ec = wizard.run(want_voice=False)

        assert ec == 0

    def test_launch_preference_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        core = _dep(present="/usr/bin/python3")
        claude = _dep(present="/usr/bin/claude", key="claude")

        with patch.object(wizard.deps, "core", return_value=[core]), \
             patch.object(wizard.deps, "backends", return_value=[claude]), \
             patch.object(wizard.deps, "voice", return_value=[]), \
             patch.object(wizard.deps, "system", return_value="macOS"), \
             patch.object(wizard.deps, "package_manager", return_value="brew"), \
             patch("builtins.input", side_effect=["1", "y"]):
            ec = wizard.run(want_voice=False)

        assert ec == 0
        data = json.loads((tmp_path / ".ledger" / "config.json").read_text(encoding="utf-8"))
        assert data["launch"] is True

    def test_multiple_core_deps_all_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        dep_a = _dep(present="/usr/bin/git")
        dep_b = _dep(present="/usr/bin/python3")
        claude = _dep(present="/usr/bin/claude", key="claude")

        with patch.object(wizard.deps, "core", return_value=[dep_a, dep_b]), \
             patch.object(wizard.deps, "backends", return_value=[claude]), \
             patch.object(wizard.deps, "voice", return_value=[]), \
             patch.object(wizard.deps, "system", return_value="Linux"), \
             patch.object(wizard.deps, "package_manager", return_value="apt"), \
             patch("builtins.input", side_effect=["4", "n"]):
            ec = wizard.run(want_voice=False)

        assert ec == 0
        # No backend chosen and no launch → no config written
        assert not (tmp_path / ".ledger" / "config.json").exists()

    def test_voice_skipped_when_want_voice_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        core = _dep(present="/usr/bin/python3")
        claude = _dep(present="/usr/bin/claude", key="claude")
        voice_dep = _dep(present=None)

        # voice() should still be called to enumerate deps, but _offer not reached
        # because want_voice=False means do_voice is immediately False
        with patch.object(wizard.deps, "core", return_value=[core]), \
             patch.object(wizard.deps, "backends", return_value=[claude]), \
             patch.object(wizard.deps, "voice", return_value=[voice_dep]) as mock_voice, \
             patch.object(wizard.deps, "system", return_value="Linux"), \
             patch.object(wizard.deps, "package_manager", return_value="apt"), \
             patch("builtins.input", side_effect=["1", "n"]):
            ec = wizard.run(want_voice=False)

        assert ec == 0
        # The voice deps should NOT be offered (present() never called on them)
        voice_dep.present.assert_not_called()


# ─── PROVIDERS constant ────────────────────────────────────────────────────────

class TestProviders:
    def test_has_expected_keys(self):
        assert set(wizard.PROVIDERS) == {"anthropic", "openai", "deepseek", "groq"}

    def test_each_entry_is_label_and_url(self):
        for name, (label, url) in wizard.PROVIDERS.items():
            assert isinstance(label, str) and label
            assert isinstance(url, str) and url.startswith("https://")
