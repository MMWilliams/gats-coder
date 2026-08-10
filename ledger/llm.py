"""Pluggable LLM backends for the L3 generative layer, the goal compiler,
and (M4+) synthesis skills.

The planner never talks to a backend directly -- backends are consulted only
through the layered world model (L3, cached per Eq. 8) and the goal compiler
(validated against the sensor vocabulary). Every consultation is metered.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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

    def __init__(self, model: str | None = None, timeout: int = 180):
        exe = shutil.which("claude")
        if exe is None:
            raise RuntimeError("claude CLI not found on PATH")
        self._exe = exe
        self._model = model
        self._timeout = timeout
        self.id = f"claude-cli:{model or 'default'}"
        self.calls = 0

    def complete(self, prompt: str) -> str:
        cmd = [self._exe, "-p", prompt, "--output-format", "text"]
        if self._model:
            cmd += ["--model", self._model]
        self.calls += 1
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self._timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {proc.stderr.strip()[:500]}")
        return proc.stdout


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
