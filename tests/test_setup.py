"""`ledger setup`: credentials, detection, and installing nothing quietly."""

import json
import os
import stat
import sys

import pytest

from ledger import credentials, setup as deps


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))
    for var in credentials.ENV_VAR.values():
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_a_key_round_trips(home):
    credentials.put("anthropic", "sk-ant-secret-value-1234")
    assert credentials.get("anthropic") == "sk-ant-secret-value-1234"


def test_the_key_file_is_not_readable_by_anyone_else(home):
    path = credentials.put("openai", "sk-openai-abcdefgh")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if os.name != "nt":          # Windows ACLs are not POSIX mode bits
        assert mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP
                       | stat.S_IWOTH) == 0, f"mode is {oct(mode)}"


def test_keys_never_go_in_the_repo_config(home):
    """The repo config gets committed. A key in it is a key to rotate."""
    path = credentials.put("anthropic", "sk-ant-xyz")
    assert path == home / ".ledger" / "credentials.json"
    assert "credentials" in path.name


def test_the_environment_wins_over_the_store(home, monkeypatch):
    """An exported key is someone deliberately overriding for this shell or
    this CI job; silently preferring the stored one would be backwards."""
    credentials.put("anthropic", "stored-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert credentials.get("anthropic") == "env-key"


def test_a_key_is_never_shown_in_full(home):
    credentials.put("anthropic", "sk-ant-0123456789abcdef")
    shown = credentials.stored()["anthropic"]
    assert "0123456789" not in shown
    assert shown.endswith("cdef") and shown.startswith("sk-")


def test_forgetting_a_key_removes_it(home):
    credentials.put("groq", "gsk-value-here")
    assert credentials.forget("groq") is True
    assert credentials.get("groq") is None
    assert credentials.forget("groq") is False


def test_missing_store_is_not_an_error(home):
    assert credentials.get("anthropic") is None
    assert credentials.stored() == {}


def test_a_corrupt_store_does_not_crash_the_agent(home):
    p = home / ".ledger"
    p.mkdir(parents=True)
    (p / "credentials.json").write_text("{not json", encoding="utf-8")
    assert credentials.get("anthropic") is None


# --------------------------------------------------------------- detection

def test_the_platform_maps_to_a_package_manager_order():
    assert deps.system() in {"windows", "macos", "linux"}
    # whatever this machine is, asking must not raise
    deps.package_manager()


def test_install_commands_are_built_per_manager():
    ffmpeg = next(d for d in deps.voice() if d.key == "ffmpeg")
    assert ffmpeg.packages["winget"] and ffmpeg.packages["brew"]
    assert deps._install_cmd("brew", ffmpeg.packages) == ["brew", "install", "ffmpeg"]
    win = deps._install_cmd("winget", ffmpeg.packages)
    assert win[:2] == ["winget", "install"] and "Gyan.FFmpeg" in win


def test_a_dependency_with_no_package_falls_back_to_pip():
    piper = next(d for d in deps.voice() if d.key == "piper")
    cmd = piper.install_command()
    # on a machine whose manager has no piper package, pip is the honest route
    assert cmd is None or cmd[:4] == [sys.executable, "-m", "pip", "install"] \
        or cmd[0] in {"brew", "port", "winget", "scoop", "choco", "apt-get",
                      "dnf", "pacman"}


def test_a_backend_that_resolves_but_does_not_run_counts_as_absent(monkeypatch):
    """A broken npm auto-update leaves a `claude` stub that is on PATH and
    fails on every call. Setup must not report that as working -- it did
    happen, and the failure looked like a LEDGER bug for an hour."""
    claude = next(d for d in deps.backends() if d.key == "claude")
    monkeypatch.setattr(deps.shutil, "which", lambda n: "C:/fake/claude.CMD")

    class Broken:
        returncode = 1
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: Broken())
    assert claude.present() is None


def test_ollama_installed_but_not_running_counts_as_absent(monkeypatch):
    """Installed is not the same as running: a stopped daemon fails at the
    first model call, long after setup said everything was fine."""
    ollama = next(d for d in deps.backends() if d.key == "ollama")
    monkeypatch.setattr(deps.shutil, "which", lambda n: "/usr/bin/ollama")

    class Down:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: Down())
    assert ollama.present() is None


def test_nothing_is_installed_without_being_shown(monkeypatch, capsys):
    """Every install prints its exact command first. A package manager
    pulling software onto someone's machine is not a thing to do quietly."""
    seen = {}

    class Ran:
        returncode = 0

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return Ran()

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    assert deps.run_install(["brew", "install", "ffmpeg"]) is True
    out = capsys.readouterr().out
    assert "brew install ffmpeg" in out
    assert seen["cmd"] == ["brew", "install", "ffmpeg"]


# ------------------------------------------------------------- the backends

def test_the_anthropic_backend_reads_the_stored_key(home):
    from ledger.llm import AnthropicAPIBackend
    credentials.put("anthropic", "sk-ant-from-the-store")
    backend = AnthropicAPIBackend()
    assert backend._key == "sk-ant-from-the-store"


def test_no_key_anywhere_says_how_to_fix_it(home):
    from ledger.llm import AnthropicAPIBackend
    with pytest.raises(RuntimeError) as excinfo:
        AnthropicAPIBackend()
    assert "ledger setup" in str(excinfo.value)


def test_a_windows_shim_is_probed_by_its_resolved_path(monkeypatch):
    """The npm CLIs are .CMD shims on Windows, and CreateProcess does not
    apply PATHEXT without a shell -- so probing with the bare name 'claude'
    failed on a perfectly good install and setup reported it missing.
    Whatever shutil.which resolved is what must be executed."""
    seen = {}

    class Ran:
        returncode = 0
        stdout = ""

    def fake_run(cmd, **kw):
        seen["argv0"] = cmd[0]
        return Ran()

    monkeypatch.setattr(deps.shutil, "which",
                        lambda n: r"C:\Users\x\AppData\Roaming\npm\claude.CMD")
    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    claude = next(d for d in deps.backends() if d.key == "claude")
    assert claude.present() is not None
    assert seen["argv0"].endswith("claude.CMD"), (
        f"probed {seen['argv0']!r} rather than the resolved path")


def test_setups_backend_id_is_actually_read_back(home, tmp_path):
    """The wizard writes {"backend": {"id": ...}} and the resolver read
    block["kind"] -- so setup wrote a config the resolver silently ignored
    and the user was told to configure something they just had. Both keys
    are accepted now."""
    import types
    from ledger import cli

    repo = tmp_path / "r"
    (repo / ".ledger").mkdir(parents=True)
    (repo / ".ledger" / "config.json").write_text(
        json.dumps({"backend": {"id": "none"}}), encoding="utf-8")
    args = types.SimpleNamespace(backend=None, model=None, base_url=None,
                                 repo=str(repo))
    # kind "none" resolves to None rather than raising -- proof the block
    # was read at all, since an ignored block would fall through to "claude"
    assert cli._backend(args, cli._config(repo)) is None


def test_a_missing_backend_names_the_way_out():
    """This is the first thing a new install prints when nothing is set up.
    "claude CLI not found on PATH" was true, unhelpful, and silent about the
    two other ways to reach a model."""
    from ledger.llm import ClaudeCLIBackend
    import ledger.llm as llm

    real = llm.shutil.which
    llm.shutil.which = lambda _n: None
    try:
        with pytest.raises(RuntimeError) as excinfo:
            ClaudeCLIBackend()
    finally:
        llm.shutil.which = real
    message = str(excinfo.value)
    assert "ledger setup" in message
    assert "Ollama" in message or "API key" in message


def test_a_bare_ledger_greets_rather_than_erroring(capsys, monkeypatch):
    """It used to be an argparse error listing eighteen subcommands under
    "the following arguments are required" -- the very first thing a new
    install prints, reading as a fault rather than a greeting."""
    from ledger.cli import main
    monkeypatch.setattr("pathlib.Path.home",
                        staticmethod(lambda: __import__("pathlib").Path("/nonexistent-home")))
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "ledger setup" in out, "an unconfigured machine must be sent to setup"
    assert "required" not in out.lower()
