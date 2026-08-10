"""Optional live smoke test for the claude CLI backend.

Skipped unless LEDGER_LIVE_LLM=1 (costs tokens, needs the CLI on PATH).
"""

import os
import shutil

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LEDGER_LIVE_LLM") != "1" or shutil.which("claude") is None,
    reason="set LEDGER_LIVE_LLM=1 (and have claude CLI) to run live",
)


def test_claude_cli_backend_completes():
    from ledger.llm import ClaudeCLIBackend, extract_json
    backend = ClaudeCLIBackend()
    out = backend.complete(
        'Respond with ONLY this JSON object: {"ok": true}')
    obj = extract_json(out)
    assert obj == {"ok": True}
