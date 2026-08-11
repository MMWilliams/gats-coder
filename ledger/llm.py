"""Pluggable LLM backends for the L3 generative layer, the goal compiler,
and (M4+) synthesis skills.

The planner never talks to a backend directly -- backends are consulted only
through the layered world model (L3, cached per Eq. 8) and the goal compiler
(validated against the sensor vocabulary). Every consultation is metered.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Callable, Protocol


class LLMBackend(Protocol):
    id: str

    def complete(self, prompt: str) -> str: ...


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
            raise RuntimeError("claude CLI not found on PATH")
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
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=self._timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {proc.stderr.strip()[:500]}")
        return proc.stdout


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
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
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
        self._key = api_key if api_key is not None \
            else os.environ.get("OPENAI_API_KEY")
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self.id = f"{id_prefix}:{model}"
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        headers = {}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        resp = _post_json(
            f"{self._base}/chat/completions", headers,
            {"model": self._model,
             "messages": [{"role": "user", "content": prompt}]},
            self._timeout)
        choices = resp.get("choices") or []
        if not choices:
            raise RuntimeError(f"no choices in response from {self._base}")
        return str((choices[0].get("message") or {}).get("content") or "")


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
