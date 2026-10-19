"""The interactive half of `ledger setup`.

Split from setup.py so the detection and install logic stays testable
without a terminal attached: everything here needs a human, everything
there does not.
"""

from __future__ import annotations

import getpass
import json
import os
import stat
from pathlib import Path

from . import credentials, setup as deps, ui

PROVIDERS = {
    "anthropic": ("Anthropic API key", "https://console.anthropic.com/settings/keys"),
    "openai": ("OpenAI API key", "https://platform.openai.com/api-keys"),
    "deepseek": ("DeepSeek API key", "https://platform.deepseek.com/api_keys"),
    "groq": ("Groq API key", "https://console.groq.com/keys"),
}


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        got = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return got or default


def _yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        got = input(f"{prompt} [{hint}] ").strip().lower()
    except EOFError:
        return default
    if not got:
        return default
    return got.startswith("y")


def _write_global(update: dict) -> Path:
    p = Path.home() / ".ledger" / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if p.is_file():
        try:
            current = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    if not isinstance(current, dict):
        current = {}
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            current[key] = {**current[key], **value}
        else:
            current[key] = value
    p.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return p


def _offer(dep, echo) -> bool:
    """Show a dependency's state and offer to install it. True if present."""
    where = dep.present()
    if where:
        echo(f"  {ui.OK} {dep.label:<12} {ui.dim(str(where))}")
        return True
    echo(f"  {ui.FAIL} {dep.label:<12} {ui.dim(dep.why)}")
    cmd = dep.install_command()
    if cmd is None:
        if dep.manual:
            echo(f"      install it from {dep.manual}")
        else:
            echo("      no package manager found to install this with")
        return False
    if not _yes(f"      install {dep.label} now?", default=False):
        return False
    ok = deps.run_install(cmd, echo=echo)
    echo(f"      {'installed' if ok else 'install failed'}")
    # Re-detect rather than trusting the exit code: a package manager can
    # report success while the binary is not yet on this shell's PATH.
    return bool(dep.present())


def _choose_backend(echo) -> dict | None:
    echo("")
    echo(ui.bold("2. How should LEDGER reach a model?"))
    echo(ui.dim("   Any one of these is enough. You can change it later with"
                " ledger setup."))
    echo("")
    options = [
        ("1", "Claude CLI      uses your existing Claude subscription, no key"),
        ("2", "API key         Anthropic, OpenAI, DeepSeek or Groq"),
        ("3", "Local model     Ollama, offline, no key and no per-token cost"),
        ("4", "skip"),
    ]
    for num, text in options:
        echo(f"   {num}. {text}")
    choice = _ask("   choose", "1")

    if choice == "1":
        dep = next(d for d in deps.backends() if d.key == "claude")
        if _offer(dep, echo):
            return {"backend": {"id": "claude"}}
        return None

    if choice == "2":
        echo("")
        for i, (name, (label, url)) in enumerate(PROVIDERS.items(), 1):
            echo(f"   {i}. {label:<20} {ui.dim(url)}")
        pick = _ask("   provider", "1")
        try:
            provider = list(PROVIDERS)[int(pick) - 1]
        except (ValueError, IndexError):
            provider = "anthropic"
        env = credentials.ENV_VAR.get(provider)
        if env and os.environ.get(env):
            echo(f"   {ui.OK} {env} is already set in this environment; "
                 "that wins over anything stored.")
        else:
            # getpass, not input: a key typed at a visible prompt ends up in
            # the terminal scrollback, and passing it as an argument would
            # put it in shell history and the process list.
            key = getpass.getpass(f"   paste your {provider} key (hidden): ").strip()
            if not key:
                echo("   nothing entered, skipping")
                return None
            credentials.put(provider, key)
            echo(f"   {ui.OK} sealed in {credentials.describe(credentials.vault())}")
            echo(ui.dim("     it never leaves this machine except to "
                        + ", ".join(sorted(credentials.PROVIDER_HOSTS.get(provider, ())))))
        backend_id = "anthropic" if provider == "anthropic" else "openai"
        cfg = {"backend": {"id": backend_id}}
        base = {"deepseek": "https://api.deepseek.com/v1",
                "groq": "https://api.groq.com/openai/v1"}.get(provider)
        if base:
            cfg["backend"]["base_url"] = base
        cfg["backend"]["provider"] = provider
        return cfg

    if choice == "3":
        dep = next(d for d in deps.backends() if d.key == "ollama")
        if not _offer(dep, echo):
            return None
        models = deps.ollama_models()
        if models:
            echo("   models already pulled: " + ", ".join(models))
            model = _ask("   which model", models[0])
        else:
            echo(ui.dim("   no models pulled yet; qwen3-coder:30b is a good "
                        "default for code (~18 GB)"))
            model = _ask("   which model to pull", "qwen3-coder:30b")
            if _yes(f"   pull {model} now? this downloads several GB",
                    default=False):
                deps.run_install(["ollama", "pull", model], echo=echo)
        return {"backend": {"id": "ollama", "model": model}}

    return None


def run(echo=print, want_voice: bool | None = None) -> int:
    echo("")
    echo(ui.bold("LEDGER setup"))
    echo(ui.dim(f"  {deps.system()}, package manager: "
                f"{deps.package_manager() or 'none found'}"))
    echo("")

    echo(ui.bold("1. Essentials"))
    ok = True
    for dep in deps.core():
        ok = _offer(dep, echo) and ok

    chosen = _choose_backend(echo)

    echo("")
    echo(ui.bold("3. Speech (optional)"))
    echo(ui.dim("   Talk to LEDGER instead of typing. Everything runs on this "
                "machine; no audio leaves it."))
    do_voice = want_voice
    if do_voice is None:
        do_voice = _yes("   set up speech?", default=False)
    if do_voice:
        for dep in deps.voice():
            _offer(dep, echo)
        from . import whisper_setup
        if whisper_setup.have_binary() and whisper_setup.have_model():
            echo(f"  {ui.OK} whisper.cpp   {ui.dim('speech-to-text ready')}")
        else:
            echo(f"  {ui.FAIL} whisper.cpp   "
                 f"{ui.dim('transcribes speech, offline')}")
            echo(ui.dim("      about 150 MB: the whisper-cli program and an "
                        "English model"))
            if _yes("      download whisper.cpp now?", default=False):
                whisper_setup.install(echo=echo)

    update = dict(chosen or {})
    echo("")
    echo(ui.bold("4. Preferences"))
    if _yes("   launch what LEDGER builds when a run succeeds?", default=True):
        update["launch"] = True
    if update:
        path = _write_global(update)
        echo(f"   {ui.OK} written to {path}")

    echo("")
    echo(ui.dim("  check it any time with: ledger doctor"))
    echo(ui.dim("  start working with:     ledger chat"))
    echo("")
    return 0 if ok else 1
