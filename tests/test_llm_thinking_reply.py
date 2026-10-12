"""Bug report, not a fix (hardbench FINDINGS #6, primes and rk45 local runs).

Ollama's OpenAI-compatible endpoint returns a thinking model's reply as
`message.content` plus `message.reasoning`. On both hard tasks qwen3.8:27b
generated 14k-21k tokens per author call and every reply came back with an
empty `content`; the backend returned "" and AuthorPatch logged "neither FILE:
blocks nor a unified diff", so 8 calls and two episodes were lost to a reply
nobody could see."""
import pytest

from ledger import llm
from ledger.llm import OpenAIAPIBackend

THOUGHT = "Let me work out the integrator. " * 400


def _reply(content, reasoning):
    return {"choices": [{"message": {"role": "assistant", "content": content,
                                     "reasoning": reasoning},
                         "finish_reason": "stop"}]}


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(llm, "_post_json",
                        lambda url, headers, payload, timeout: _reply("", THOUGHT))
    return OpenAIAPIBackend(model="qwen3.8:27b", api_key="",
                            base_url="http://127.0.0.1:11434/v1", id_prefix="ollama")


def test_a_reply_that_never_left_its_thinking_is_not_silently_empty(backend):
    """Either the caller gets usable text or it is told what happened."""
    try:
        text = backend.complete("write the patch")
    except Exception as e:  # noqa: BLE001 -- any error that says why is fine
        assert any(w in str(e).lower() for w in ("empty", "reasoning", "thinking"))
    else:
        assert text.strip()
