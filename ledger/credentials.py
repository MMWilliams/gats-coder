"""API keys on disk, in one place, with the obvious mistakes closed off.

Kept deliberately separate from `.ledger/config.json`: that file lives in
the repo and gets committed, and a key in a committed file is a key you
have to rotate. This one lives under the user's home directory, is created
0600, and is never echoed back in full.

Environment variables still win. A key exported into the shell is usually
someone deliberately overriding for one command or one CI job, and silently
preferring a stored key over that would be the wrong way round.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

# provider -> the environment variable that overrides the stored key
ENV_VAR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
}


def path() -> Path:
    return Path.home() / ".ledger" / "credentials.json"


def _read() -> dict:
    p = path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def get(provider: str) -> str | None:
    """The key for a provider: environment first, then the store."""
    env = ENV_VAR.get(provider)
    if env:
        from_env = os.environ.get(env)
        if from_env:
            return from_env
    value = _read().get(provider)
    return value if isinstance(value, str) and value else None


def put(provider: str, key: str) -> Path:
    """Store a key, readable only by this user."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _read()
    data[provider] = key
    # Create with tight permissions BEFORE writing: opening 0644 and
    # chmod-ing afterwards leaves a window where the key is world-readable.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:                     # pragma: no cover - exotic filesystems
        pass
    return p


def forget(provider: str) -> bool:
    data = _read()
    if provider not in data:
        return False
    del data[provider]
    p = path()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return True


def mask(key: str) -> str:
    """A key rendered for a human: enough to recognise, useless to steal."""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}...{key[-4:]}"


def stored() -> dict[str, str]:
    """provider -> masked key, for display."""
    return {k: mask(v) for k, v in _read().items() if isinstance(v, str)}
