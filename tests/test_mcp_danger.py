"""MCP stdio client, the chat tool loop, and --dangerously-skip-permissions."""

import json
import sys
from types import SimpleNamespace

from ledger.chat import Conversation
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.mcp import MCPServer, load_servers

import ledger.cli as cli

STUB = r'''
import json, sys
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"protocolVersion": "2025-06-18",
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "stub", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "echo", "description": "echo the text back",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string"}}}}]}})
    elif method == "tools/call":
        args = msg["params"].get("arguments") or {}
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text",
                         "text": "ECHO: " + str(args.get("text", ""))}]}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": "no such method"}})
'''


def _stub_path(tmp_path):
    p = tmp_path / "mcp_stub.py"
    p.write_text(STUB, encoding="utf-8")
    return p


# -- client ------------------------------------------------------------------

def test_mcp_server_handshake_and_call(tmp_path):
    srv = MCPServer("stub", [sys.executable, str(_stub_path(tmp_path))])
    try:
        assert [t.name for t in srv.tools] == ["echo"]
        assert srv.tools[0].description == "echo the text back"
        assert srv.call("echo", {"text": "hi"}) == "ECHO: hi"
    finally:
        srv.close()


def test_load_servers_skips_broken(tmp_path):
    errors = []
    cfg = {"mcp": {"servers": {
        "good": {"command": [sys.executable, str(_stub_path(tmp_path))]},
        "bad": {"command": [sys.executable, "-c", "import sys; sys.exit(1)"],
                "timeout": 2},
    }}}
    servers = load_servers(cfg, on_error=errors.append)
    try:
        assert "good" in servers
        assert "bad" not in servers
        assert errors and "bad" in errors[0]
    finally:
        for s in servers.values():
            s.close()


# -- the chat tool loop ------------------------------------------------------

def _tooled_conv(tmp_path, tool_result="ECHO: hi", journal=None):
    calls = []

    def scripted(prompt):
        if "TOOL RESULT docs:echo" in prompt:
            return "the tool said: ECHO: hi"
        if "AVAILABLE TOOLS" in prompt:
            return json.dumps({"tool": "docs:echo",
                               "arguments": {"text": "hi"}})
        return "plain answer"

    conv = Conversation(backend=FakeBackend(scripted), journal=journal)
    conv.tools = {"docs:echo": "echo the text back"}

    def tool_call(name, args):
        calls.append((name, args))
        return tool_result

    conv.tool_call = tool_call
    return conv, calls


def test_tool_loop_calls_and_continues(tmp_path):
    journal_path = tmp_path / "chat.jsonl"
    with Journal(str(journal_path)) as j:
        conv, calls = _tooled_conv(tmp_path, journal=j)
        reply = conv.ask("what does the echo tool say for hi?")
    assert reply == "the tool said: ECHO: hi"
    assert calls == [("docs:echo", {"text": "hi"})]
    kinds = [e["t"] for e in Journal.read(str(journal_path))]
    assert "MCP_CALL" in kinds and "MCP_RESULT" in kinds
    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err


def test_tool_denial_is_surfaced_to_the_model(tmp_path):
    conv, calls = _tooled_conv(
        tmp_path, tool_result="DENIED by the permission gate")
    seen = []
    orig = conv.backend._responder

    def spy(prompt):
        seen.append(prompt)
        return orig(prompt)

    conv.backend._responder = spy
    conv.ask("what does the echo tool say for hi?")
    assert any("DENIED by the permission gate" in p for p in seen)


def test_reply_without_tool_json_is_final():
    conv = Conversation(backend=FakeBackend("plain answer"))
    conv.tools = {"docs:echo": "x"}
    conv.tool_call = lambda n, a: (_ for _ in ()).throw(
        AssertionError("must not be called"))
    assert conv.ask("hello there") == "plain answer"


# -- dangerously skip permissions -------------------------------------------

def test_danger_flag_bypasses_everything(capsys):
    args = SimpleNamespace(profile="attended", yes=False,
                           dangerously_skip_permissions=True,
                           deny="PushBranch", allow=None)
    cfg = {"policy": {"deny": ["PushBranch"]}}
    gate, profile = cli._gate_and_profile(args, cfg)
    assert gate("PushBranch", "network") is True   # even the deny rule
    assert gate("Commit", "irreversible") is True
    assert "permissions disabled" in capsys.readouterr().err


def test_without_danger_flag_deny_still_wins():
    args = SimpleNamespace(profile="attended", yes=True,
                           dangerously_skip_permissions=False,
                           deny="PushBranch", allow=None)
    gate, _ = cli._gate_and_profile(args, {})
    assert gate("PushBranch", "network") is False


# -- backend failures are non-effects, never crashes -------------------------

def test_raising_backend_escalates_instead_of_crashing(tmp_path):
    import subprocess as sp

    from conftest import TEST_CMD, needs_git  # noqa: F401
    from ledger.executor import run_repo_episode

    repo = tmp_path / "r"
    repo.mkdir()
    sp.run(["git", "init", "-qb", "main", str(repo)], check=True,
           capture_output=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n",
                                  encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n"
        "    assert add(2, 3) == 5\n", encoding="utf-8")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True,
           capture_output=True)
    sp.run(["git", "-C", str(repo), "-c", "user.email=t@t",
            "-c", "user.name=t", "commit", "-qm", "seed"], check=True,
           capture_output=True)

    class Exploding:
        id = "exploding"
        calls = 0

        def complete(self, prompt):
            raise TimeoutError("simulated backend timeout")

    result, executor, journal_path = run_repo_episode(
        repo, tmp_path / "ep", frozenset({"tests.pass:unit"}), TEST_CMD,
        backend=Exploding(), slots=["a"], max_author_attempts=2)
    assert not result.success                      # no crash, a clean failure
    entries = Journal.read(str(journal_path))
    esc = [e for e in entries if e["t"] == "ESCALATION"]
    assert esc and esc[0]["reason"] == "budget_exhausted"
    ok, err = Journal.verify_chain(str(journal_path))
    assert ok, err
