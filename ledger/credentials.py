"""API keys: kept on this machine, encrypted at rest, sent only where they
belong, and scrubbed from everything LEDGER writes or sends.

The guarantees, each enforced here or at the one place that needs it:

1. At rest a key is held by the operating system's own secret store --
   DPAPI on Windows (encrypted to this user account; a copied file is
   useless on another machine or account), the login Keychain on macOS, the
   Secret Service keyring on Linux. Only where none exists does it fall
   back to a file readable by this user alone (0600), and setup says so.
2. A key travels only to its provider's API host, over HTTPS, or to a host
   the user bound to it themselves (`ledger setup --key openai --host ...`).
   A base URL that came from a repository's `.ledger/config.json` -- a file
   anyone can commit -- never receives a key it was not bound to.
3. Keys never reach a subprocess LEDGER starts for a repository (tests,
   hooks, MCP servers, launches): `child_env` strips them.
4. Every key LEDGER loads is registered for scrubbing: journals, blobs,
   receipts, prompts sent to a model, and error messages replace it with
   [REDACTED] by exact value, whatever its shape.
5. Keys are typed at a hidden prompt, never taken on the command line
   (argv is visible to every process on the machine), and shown masked.

Environment variables still win over the store: an exported key is usually
someone deliberately overriding for one command or one CI job.

Kept apart from `.ledger/config.json` on purpose: that file lives in the
repo and gets committed, and a key in a committed file is a key you have to
rotate.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.parse
from pathlib import Path

# provider -> the environment variable that overrides the stored key
ENV_VAR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "brave": "BRAVE_API_KEY",
}

# provider -> the API hosts that may receive its key without being asked
PROVIDER_HOSTS = {
    "anthropic": {"api.anthropic.com"},
    "openai": {"api.openai.com"},
    "deepseek": {"api.deepseek.com"},
    "groq": {"api.groq.com"},
    "kimi": {"api.moonshot.ai", "api.moonshot.cn"},
    "brave": {"api.search.brave.com"},
}

SERVICE = "ledger-agent"
_ENTROPY = b"ledger-agent credentials v2"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}

# Every secret this process has handed out, for exact-value scrubbing.
_LOADED: set[str] = set()


def path() -> Path:
    return Path.home() / ".ledger" / "credentials.json"


# -- vaults ------------------------------------------------------------------

class VaultError(RuntimeError):
    pass


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes) -> BLOB:
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(BLOB), wintypes.LPCWSTR, ctypes.POINTER(BLOB),
                   wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD,
                   ctypes.POINTER(BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    src, entropy, out = blob(data), blob(_ENTROPY), BLOB()
    ui_forbidden = 0x1
    if not fn(ctypes.byref(src), "ledger" if protect else None,
              ctypes.byref(entropy), None, None, ui_forbidden, ctypes.byref(out)):
        raise VaultError(f"DPAPI failed ({ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, wintypes.HLOCAL))


def _run(cmd: list[str], stdin: str = "") -> str:
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                       timeout=20)
    if p.returncode != 0:
        raise VaultError(f"{cmd[0]} exited {p.returncode}")
    return p.stdout


def vault() -> str:
    """The store this machine uses: dpapi, keychain, secret-service, file."""
    forced = os.environ.get("LEDGER_VAULT")
    if forced:
        return forced
    if sys.platform == "win32":
        return "dpapi"
    if sys.platform == "darwin" and shutil.which("security"):
        return "keychain"
    if shutil.which("secret-tool") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return "secret-service"
    return "file"


def _safe_for_keychain(key: str) -> bool:
    # `security -i` parses its stdin like a shell line; keys are plain
    # tokens, and anything else goes to the file vault rather than risk a
    # mis-parse.
    return key.isprintable() and not any(c in key for c in " \t'\"\\")


def _seal(provider: str, key: str, kind: str) -> dict:
    if kind == "dpapi":
        return {"vault": "dpapi",
                "data": base64.b64encode(_dpapi(key.encode(), True)).decode()}
    if kind == "keychain" and _safe_for_keychain(key):
        # Through stdin, never argv: argv is readable by every process.
        _run(["security", "-i"],
             f'add-generic-password -U -s {SERVICE} -a {provider} -w "{key}"\n')
        return {"vault": "keychain"}
    if kind == "secret-service":
        _run(["secret-tool", "store", f"--label=LEDGER {provider} key",
              "service", SERVICE, "account", provider], key)
        return {"vault": "secret-service"}
    return {"vault": "file", "data": key}


def _open(provider: str, entry: dict) -> str | None:
    kind = entry.get("vault")
    try:
        if kind == "dpapi":
            return _dpapi(base64.b64decode(entry["data"]), False).decode()
        if kind == "keychain":
            return _run(["security", "find-generic-password", "-s", SERVICE,
                         "-a", provider, "-w"]).strip() or None
        if kind == "secret-service":
            return _run(["secret-tool", "lookup", "service", SERVICE,
                         "account", provider]).strip() or None
        if kind == "file":
            return entry.get("data") or None
    except (VaultError, OSError, ValueError, KeyError,
            subprocess.TimeoutExpired):
        return None
    return None


def _drop(provider: str, entry: dict) -> None:
    try:
        if entry.get("vault") == "keychain":
            _run(["security", "delete-generic-password", "-s", SERVICE,
                  "-a", provider])
        elif entry.get("vault") == "secret-service":
            _run(["secret-tool", "clear", "service", SERVICE, "account", provider])
    except (VaultError, OSError, subprocess.TimeoutExpired):
        pass


# -- the index file ------------------------------------------------------------

def _read() -> dict:
    p = path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("version") == 2 and isinstance(data.get("keys"), dict):
        return {k: v for k, v in data["keys"].items() if isinstance(v, dict)}
    # v1 stored {provider: "plaintext key"}; read it, rewrite it sealed.
    legacy = {k: {"vault": "file", "data": v} for k, v in data.items()
              if isinstance(v, str) and v}
    if legacy:
        _migrate(legacy)
    return legacy


def _write(entries: dict) -> Path:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Create with tight permissions BEFORE writing: opening 0644 and
    # chmod-ing afterwards leaves a window where the file is world-readable.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"version": 2, "keys": entries}, fh, indent=2)
        fh.write("\n")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:                     # pragma: no cover - exotic filesystems
        pass
    return p


def _migrate(legacy: dict) -> None:
    kind = vault()
    if kind == "file":
        return
    sealed = {}
    for provider, entry in legacy.items():
        try:
            sealed[provider] = _seal(provider, entry["data"], kind)
        except (VaultError, OSError, subprocess.TimeoutExpired):
            sealed[provider] = entry
    _write(sealed)


# -- public API ----------------------------------------------------------------

def _remember(key: str | None) -> str | None:
    if key and len(key) >= 8:
        _LOADED.add(key)
    return key


def get(provider: str) -> str | None:
    """The key for a provider: environment first, then the store."""
    env = ENV_VAR.get(provider)
    if env and os.environ.get(env):
        return _remember(os.environ[env])
    entry = _read().get(provider)
    return _remember(_open(provider, entry)) if entry else None


def put(provider: str, key: str, hosts: list[str] | None = None) -> Path:
    """Seal a key into this machine's vault. `hosts` binds extra API hosts
    (a self-hosted gateway) that may receive it."""
    entries = _read()
    old = entries.get(provider)
    if old:
        _drop(provider, old)
    kind = vault()
    try:
        entry = _seal(provider, key, kind)
    except (VaultError, OSError, subprocess.TimeoutExpired):
        entry = _seal(provider, key, "file")
    bound = set((old or {}).get("hosts") or []) | {h.lower() for h in hosts or []}
    if bound:
        entry["hosts"] = sorted(bound)
    entries[provider] = entry
    _remember(key)
    return _write(entries)


def bind(provider: str, hosts: list[str]) -> None:
    """Let a stored key also go to `hosts` (a gateway the user runs)."""
    entries = _read()
    entry = entries.get(provider)
    if entry is None:
        raise KeyError(provider)
    entry["hosts"] = sorted(set(entry.get("hosts") or [])
                            | {h.lower() for h in hosts})
    _write(entries)


def forget(provider: str) -> bool:
    entries = _read()
    if provider not in entries:
        return False
    _drop(provider, entries.pop(provider))
    _write(entries)
    return True


def describe(kind: str) -> str:
    if kind == "file":
        return f"{path()} (owner-only file; no OS keyring found)"
    return {"dpapi": "Windows DPAPI (encrypted to this account)",
            "keychain": "the macOS Keychain",
            "secret-service": "the system keyring (Secret Service)"}.get(kind, "")


def where(provider: str) -> str:
    """Which vault holds a provider's key, for display ("" if none)."""
    entry = _read().get(provider) or {}
    return describe(entry.get("vault", ""))


def mask(key: str) -> str:
    """A key rendered for a human: enough to recognise, useless to steal."""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}...{key[-4:]}"


def stored() -> dict[str, str]:
    """provider -> masked key, for display."""
    out = {}
    for provider, entry in _read().items():
        key = _open(provider, entry)
        if key:
            out[provider] = mask(_remember(key))
    return out


def bound_hosts(provider: str) -> set[str]:
    entry = _read().get(provider) or {}
    return set(PROVIDER_HOSTS.get(provider, set())) | set(entry.get("hosts") or [])


def key_for_url(provider: str, url: str, trusted_url: bool = False) -> str | None:
    """The key to attach to a request for `url`, or None.

    A stored or exported key goes to its provider's own host, or to a host
    the user bound to it. `trusted_url` (the URL came from the command line
    or the user's own ~/.ledger/config.json, not a repository) lets an
    EXPORTED key follow it -- someone who typed both chose that. HTTPS is
    required unless the host is this machine.
    """
    parts = urllib.parse.urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" and host not in _LOOPBACK:
        return None
    if host in bound_hosts(provider):
        return get(provider)
    env = ENV_VAR.get(provider)
    if trusted_url and env and os.environ.get(env):
        return _remember(os.environ[env])
    return None


def provider_for_url(url: str) -> str | None:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    for provider in PROVIDER_HOSTS:
        if host in bound_hosts(provider):
            return provider
    return None


def secrets() -> set[str]:
    """Every key value this process could know, for scrubbing."""
    known = set(_LOADED)
    for var in ENV_VAR.values():
        value = os.environ.get(var)
        if value and len(value) >= 8:
            known.add(value)
    return known


def scrub(text: str) -> str:
    """`text` with every known key replaced by [REDACTED], longest first so
    a key that contains another is removed whole."""
    if not text:
        return text
    for value in sorted(secrets(), key=len, reverse=True):
        if value in text:
            text = text.replace(value, "[REDACTED]")
    return text


def child_env(env: dict | None = None) -> dict:
    """The environment for a process LEDGER starts on a repository's behalf:
    the caller's, minus every provider key. A test suite, hook or MCP
    server from a cloned repo has no business reading them."""
    out = dict(os.environ if env is None else env)
    for var in ENV_VAR.values():
        out.pop(var, None)
    return out
