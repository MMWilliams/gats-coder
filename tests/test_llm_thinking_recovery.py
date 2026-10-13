"""Bug report, not a fix (hardbench FINDINGS #6, primes and rk45 local runs).

qwen3.8:27b on Ollama 0.34 sometimes thinks for 14k-21k tokens and never
writes a visible reply. Since 20a19e0 the backend says so instead of
returning "", but the author call still yields nothing. Measured on that
server: `reasoning_effort: "low"` is ignored for this model (more tokens than
the default), and `reasoning_effort: "none"` is the only switch it honors
(no thinking, a visible reply in ~1 s). There is no thinking budget.

Wanted: thinking stays on by default, and a thinking-only reply is recovered
into a visible one instead of failing the call. The stub below behaves like
that server."""
import contextlib

import pytest

from ledger import llm
from ledger.llm import OpenAIAPIBackend

THOUGHT = "Let me reconsider the step-size controller once more. " * 300


class _ThinkingServer:
    def __init__(self):
        self.payloads = []

    def __call__(self, url, headers, payload, timeout):
        self.payloads.append(payload)
        thinking_off = payload.get("reasoning_effort") == "none"
        content = "FILE: x.py\n```python\nX = 1\n```\n" if thinking_off else ""
        reasoning = "" if thinking_off else THOUGHT
        return {"choices": [{"message": {"role": "assistant", "content": content,
                                         "reasoning": reasoning},
                             "finish_reason": "stop"}]}


@pytest.fixture
def server(monkeypatch):
    s = _ThinkingServer()
    monkeypatch.setattr(llm, "_post_json", s)
    return s


def _backend():
    return OpenAIAPIBackend(model="qwen3.8:27b", api_key="",
                            base_url="http://127.0.0.1:11434/v1", id_prefix="ollama")


def test_a_thinking_only_reply_is_recovered_into_a_visible_one(server):
    assert "FILE: x.py" in _backend().complete("write the patch")


def test_thinking_stays_on_for_the_first_request(server):
    """Guards the recovery: turning thinking off everywhere would also make
    the test above pass, and costs the model its reasoning on hard tasks."""
    with contextlib.suppress(RuntimeError):
        _backend().complete("write the patch")
    assert server.payloads[0].get("reasoning_effort") != "none"
