"""Pluggable LLM backends for the L3 generative layer, the goal compiler,
and (M4+) synthesis skills.

The planner never talks to a backend directly -- backends are consulted only
through the layered world model (L3, cached per Eq. 8) and the goal compiler
(validated against the sensor vocabulary). Every consultation is metered.
"""

from __future__ import annotations

import copy
import base64
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol


def kill_process_tree(pid: int) -> None:
    """Kill a process and everything it spawned.

    Needed because a CLI backend may be a shell shim: killing the direct
    child leaves the real worker running, detached, holding our pipes and
    spending money.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True)
        return
    import signal
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


class LLMBackend(Protocol):
    id: str

    def complete(self, prompt: str) -> str: ...


# Substrings seen in real outages: HTTP 5xx (the provider itself is broken),
# urllib's own "unreachable" wording for a refused/unresolved connection, a
# client-side timeout, and the llama.cpp/CUDA allocation-failure phrases
# Ollama passes through verbatim in a 500 body when the host is out of
# memory. Distinguishing these from "the model wrote a bad patch" matters:
# only these justify backing off instead of retrying immediately.
_BACKEND_DOWN_MARKERS = (
    "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
    "unreachable:", "unavailable", "timed out",
    "Connection refused", "Connection reset",
    "out of memory", "bad_alloc", "cudaMalloc failed",
    "has terminated", "MemoryError",
)


def is_backend_unavailable(message: str) -> bool:
    """True when a backend failure means "the server is broken", not "the
    model's answer was bad". A dead local model server keeps failing the
    same way every call; callers use this to back off instead of hammering
    it (see skills_llm.AuthorPatch and modes.run_until)."""
    return any(marker in message for marker in _BACKEND_DOWN_MARKERS)


class FakeBackend:
    """Deterministic test backend: a callable prompt -> response, or a
    static response string."""

    def __init__(self, responder: Callable[[str], str] | str, id: str = "fake"):
        self._responder = responder
        self.id = id
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if callable(self._responder):
            return self._responder(prompt)
        return self._responder


class ClaudeCLIBackend:
    """Shells out to the local `claude` CLI (claude -p). Suited to attended
    use on this machine; API-based backends can implement the same protocol.
    """

    def __init__(self, model: str | None = None, timeout: int = 600):
        # 600s: authoring a multi-file diff routinely outruns shorter caps.
        exe = shutil.which("claude")
        if exe is None:
            # This is the first thing a new install says when nothing is
            # configured, so it has to name the way out. "claude CLI not
            # found on PATH" was a dead end: true, unhelpful, and silent
            # about the two other ways to reach a model.
            raise RuntimeError(
                "no model backend configured (the claude CLI is not on "
                "PATH).\n\n  run:  ledger setup\n\n"
                "  It offers three ways, any one is enough: the Claude CLI "
                "(uses your\n  existing subscription), an API key, or Ollama "
                "for local models.")
        self._exe = exe
        self._model = model
        self._timeout = timeout
        self.id = f"claude-cli:{model or 'default'}"
        self.calls = 0

    def complete(self, prompt: str) -> str:
        # Prompt goes over stdin: on Windows `claude` resolves to an npm .cmd
        # shim parsed by cmd.exe, which truncates argv at the first newline.
        cmd = [self._exe, "-p", "--output-format", "text"]
        if self._model:
            cmd += ["--model", self._model]
        self.calls += 1
        # encoding pinned: claude emits UTF-8; text=True alone would decode
        # with the Windows locale codepage and mojibake non-ASCII output.
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace")
        try:
            out, err = proc.communicate(prompt, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            # The shim is cmd.exe and the model runs as its grandchild, so
            # killing the direct child alone orphans a running model call
            # (it keeps burning quota, and it holds the pipes this would
            # otherwise block on). Take the whole tree down.
            kill_process_tree(proc.pid)
            try:
                proc.communicate(timeout=15)
            except Exception:
                pass
            raise RuntimeError(
                f"claude CLI timed out after {self._timeout}s")
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {(err or '').strip()[:500]}")
        return out


def _post_json(url: str, headers: dict, payload: dict,
               timeout: int) -> dict:
    """POST JSON, return parsed JSON. stdlib-only on purpose -- the package
    has zero dependencies and provider SDKs would change that."""
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"{url} -> HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{url} unreachable: {exc.reason}") from exc


class AnthropicAPIBackend:
    """Direct Claude API (no CLI). Key from ANTHROPIC_API_KEY unless given."""

    def __init__(self, model: str = "claude-sonnet-5",
                 api_key: str | None = None,
                 base_url: str | None = None, timeout: int = 180,
                 max_tokens: int = 8192):
        # Environment first (an exported key is a deliberate override for
        # this shell), then whatever `ledger setup` stored. Before the store
        # existed, the only way to configure this was an environment
        # variable that vanished with the terminal.
        from .credentials import get as _stored_key
        self._key = api_key or _stored_key("anthropic")
        if not self._key:
            raise RuntimeError(
                "no Anthropic key: set ANTHROPIC_API_KEY, or run "
                "`ledger setup --key anthropic`")
        self._base = (base_url or "https://api.anthropic.com").rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self.id = f"anthropic:{model}"
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        resp = _post_json(
            f"{self._base}/v1/messages",
            {"x-api-key": self._key, "anthropic-version": "2023-06-01"},
            {"model": self._model, "max_tokens": self._max_tokens,
             "messages": [{"role": "user", "content": prompt}]},
            self._timeout)
        parts = resp.get("content") or []
        return "".join(p.get("text", "") for p in parts
                       if p.get("type") == "text")


class OpenAIAPIBackend:
    """Chat-completions protocol -- OpenAI itself and everything that speaks
    its dialect: Ollama, vLLM, Groq, DeepSeek, Kimi, Mistral, Together...
    Point base_url at the provider; key from OPENAI_API_KEY unless given
    (local endpoints may need none)."""

    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str = "https://api.openai.com/v1",
                 timeout: int = 180, id_prefix: str = "openai"):
        from .credentials import get as _stored_key
        self._key = api_key if api_key is not None else _stored_key("openai")
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self.id = f"{id_prefix}:{model}"
        self.calls = 0
        self.supports_vision = any(name in model.lower() for name in
                                   ("qwen3.8", "qwen3-vl", "gemma4"))

    def complete(self, prompt: str) -> str:
        return self._complete(prompt)

    def complete_with_image(self, prompt: str, image_path: Path) -> str:
        image_path = Path(image_path)
        image_bytes = image_path.read_bytes()
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(image_bytes) > 5_000_000:
            raise ValueError("preview must be a PNG smaller than 5 MB")
        image_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
        return self._complete(prompt, image_url=image_url)

    def _complete(self, prompt: str, image_url: str | None = None) -> str:
        self.calls += 1
        headers = {}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        content = ([{"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": image_url}]
                   if image_url else prompt)
        payload = {"model": self._model,
                   "messages": [{"role": "user", "content": content}]}
        # Bonsai's OpenAI-compatible endpoint otherwise leaves thinking
        # unbounded for requests without a per-call budget. Structured Ledger
        # outputs can exhaust the response limit before the answer begins.
        if "bonsai" in self._model.lower():
            payload["thinking_budget_tokens"] = 512
        resp = _post_json(
            f"{self._base}/chat/completions", headers, payload,
            self._timeout)
        choices = resp.get("choices") or []
        if not choices:
            raise RuntimeError(f"no choices in response from {self._base}")
        return str((choices[0].get("message") or {}).get("content") or "")

    def health_check(self, timeout: float = 5.0) -> bool:
        """Cheap reachability probe (no completion tokens spent).

        The OpenAI-compatible `/models` route is served by Ollama, vLLM,
        and every provider this class targets, so this needs no per-provider
        branching. Used to fail an attempt fast instead of waiting out a
        full completion timeout against a server that is already down.
        """
        headers = {}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        req = urllib.request.Request(f"{self._base}/models", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500
        except (urllib.error.URLError, OSError, TimeoutError):
            return False


# Writing a whole application is not the same call as compiling a one-line
# goal contract, and one flat deadline cannot serve both. Eight consecutive
# "timed out after 600s" burned an entire episode's budget without a single
# line of code being written, on a task a model needs fifteen minutes for.
AUTHOR_TIMEOUT = 1800


def with_timeout(backend, seconds: int):
    """A shallow copy of a backend with a different deadline.

    A copy rather than a mutation: the same backend object is shared with
    the goal compiler and the planner's L3 layer, and a long authoring
    deadline must not become a long deadline for everything else -- a hung
    backend would then stall the run for half an hour before anyone noticed.

    `seconds=0` removes the deadline: the authoring call waits as long as
    the model takes.
    """
    if backend is None or not hasattr(backend, "_timeout"):
        return backend
    clone = copy.copy(backend)
    clone._timeout = int(seconds) or None
    return clone


def resolve_backend(kind: str, model: str | None = None,
                    base_url: str | None = None,
                    timeout: int | None = None):
    """One resolver for every surface (run/chat/review/session specs).
    Returns None for kind='none'."""
    kind = (kind or "claude").lower()
    if kind == "none":
        return None
    if kind == "claude":
        return ClaudeCLIBackend(model=model, timeout=timeout or 600)
    if kind == "anthropic":
        return AnthropicAPIBackend(model=model or "claude-sonnet-5",
                                   base_url=base_url,
                                   timeout=timeout or 300)
    if kind == "openai":
        if not model:
            raise RuntimeError("backend 'openai' needs a model "
                               "(e.g. --model gpt-5.2)")
        return OpenAIAPIBackend(model=model,
                                base_url=base_url
                                or "https://api.openai.com/v1",
                                timeout=timeout or 300)
    if kind == "ollama":
        if not model:
            raise RuntimeError("backend 'ollama' needs a model "
                               "(e.g. --model qwen3-coder)")
        return OpenAIAPIBackend(model=model, api_key="",
                                base_url=base_url
                                or "http://localhost:11434/v1",
                                id_prefix="ollama",
                                timeout=timeout or 600)
    raise RuntimeError(f"unknown backend {kind!r} "
                       "(claude, anthropic, openai, ollama, none)")


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model response (tolerates prose
    and code fences around it)."""
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m is None:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
