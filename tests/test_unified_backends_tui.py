"""The unified session (chat that executes), multi-provider backends, and
the TUI editor state machine."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from conftest import GOAL, TEST_CMD, needs_git

import ledger.cli as cli
from ledger import tui
from ledger.chat import Conversation, repl
from ledger.journal import Journal
from ledger.llm import (AnthropicAPIBackend, FakeBackend, OpenAIAPIBackend,
                        resolve_backend)

FIX_DIFF = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n-    return a - b\n+    return a + b\n")


# -- multi-provider backends -------------------------------------------------

class _Stub(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(
            int(self.headers["Content-Length"])))
        headers = {k.lower(): v for k, v in self.headers.items()}
        _Stub.seen.append((self.path, headers, body))
        if self.path.endswith("/v1/messages"):
            out = {"content": [{"type": "text", "text": "anthropic says hi"}]}
        else:
            out = {"choices": [{"message": {"content": "openai says hi"}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # keep test output clean
        pass


@pytest.fixture
def stub_server():
    _Stub.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_anthropic_backend(stub_server):
    b = AnthropicAPIBackend(model="claude-sonnet-5", api_key="k",
                            base_url=stub_server)
    assert b.complete("hello") == "anthropic says hi"
    path, headers, body = _Stub.seen[0]
    assert path == "/v1/messages"
    assert headers["x-api-key"] == "k"
    assert body["model"] == "claude-sonnet-5"
    assert body["messages"][0]["content"] == "hello"


def test_openai_backend_with_and_without_key(stub_server):
    b = OpenAIAPIBackend(model="gpt-x", api_key="sekrit",
                         base_url=stub_server)
    assert b.complete("hi") == "openai says hi"
    assert _Stub.seen[-1][1]["authorization"] == "Bearer sekrit"
    keyless = OpenAIAPIBackend(model="qwen3-coder", api_key="",
                               base_url=stub_server, id_prefix="ollama")
    assert keyless.complete("hi") == "openai says hi"
    assert "authorization" not in _Stub.seen[-1][1]
    assert keyless.id == "ollama:qwen3-coder"


def test_resolve_backend_matrix(monkeypatch):
    assert resolve_backend("none") is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert resolve_backend("anthropic").id == "anthropic:claude-sonnet-5"
    with pytest.raises(RuntimeError):
        resolve_backend("openai")           # model required
    with pytest.raises(RuntimeError):
        resolve_backend("ollama")           # model required
    with pytest.raises(RuntimeError):
        resolve_backend("skynet")
    assert resolve_backend("ollama", "qwen3-coder").id == "ollama:qwen3-coder"


def test_missing_anthropic_key_is_loud(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        AnthropicAPIBackend()


# -- intent routing ----------------------------------------------------------

def _router(reply_for_chat="just chatting"):
    def responder(prompt):
        if "route messages" in prompt.lower():
            if "please fix the bug" in prompt:
                return json.dumps({"intent": "change",
                                   "task": "fix the bug in calc.py"})
            return json.dumps({"intent": "chat", "task": ""})
        return reply_for_chat
    return FakeBackend(responder)


def test_classify_change_and_chat():
    conv = Conversation(backend=_router())
    assert conv.classify("please fix the bug") == \
        ("change", "fix the bug in calc.py")
    assert conv.classify("what is the planner?") == ("chat", "")


def test_classify_fails_safe():
    conv = Conversation(backend=FakeBackend("not json at all"))
    assert conv.classify("anything") == ("chat", "")


def test_repl_runs_episode_inline(tmp_path):
    ran, outputs = [], []

    def runner(task):
        ran.append(task)
        return {"success": True, "plan": ["RunSuite"], "steps": 1,
                "incidents": 0, "escalation": None, "journal": "j.jsonl",
                "episode": "e", "resumed": False,
                "summary_plain": "Episode 'e' succeeded",
                "summary_styled": "styled summary"}

    inputs = iter(["please fix the bug", "", "/exit"])  # "" = default Yes
    rc = repl(Conversation(backend=_router()), tmp_path,
              input_fn=lambda p: next(inputs), output_fn=outputs.append,
              runner=runner)
    assert rc == 0
    assert ran == ["fix the bug in calc.py"]
    assert any("styled summary" in o for o in outputs)


def test_repl_decline_skips_episode(tmp_path):
    ran, outputs = [], []
    inputs = iter(["please fix the bug", "n", "/exit"])
    repl(Conversation(backend=_router()), tmp_path,
         input_fn=lambda p: next(inputs), output_fn=outputs.append,
         runner=lambda t: ran.append(t))
    assert ran == []
    assert any("/do" in o for o in outputs)


def test_repl_do_bypasses_router(tmp_path):
    ran = []

    def runner(task):
        ran.append(task)
        return {"success": True, "plan": [], "steps": 0, "incidents": 0,
                "escalation": None, "journal": "", "episode": "e",
                "resumed": False, "summary_plain": "ok",
                "summary_styled": "ok"}

    backend = FakeBackend("should never be asked")
    inputs = iter(["/do add feature x", "/exit"])
    repl(Conversation(backend=backend), tmp_path,
         input_fn=lambda p: next(inputs), output_fn=lambda s: None,
         runner=runner)
    assert ran == ["add feature x"]
    assert backend.calls == 0  # no routing call, no chat call


def test_repl_chat_message_stays_chat(tmp_path):
    outputs = []
    inputs = iter(["what is the planner?", "/exit"])
    repl(Conversation(backend=_router("it plans")), tmp_path,
         input_fn=lambda p: next(inputs), output_fn=outputs.append,
         runner=lambda t: (_ for _ in ()).throw(AssertionError("no episode")))
    assert any("it plans" in o for o in outputs)


# -- automatic episode tracking ----------------------------------------------

def _write_end(episode_dir, success):
    episode_dir.mkdir(parents=True, exist_ok=True)
    with Journal(str(episode_dir / "episode.jsonl")) as j:
        j.append("HEADER", task_id="t")
        j.append("END", success=success, plan=[], steps=0,
                 llm_planning_calls=0)


def test_auto_episode_naming_and_resume(tmp_path):
    root = tmp_path
    name, resume = cli._auto_episode(root, "Fix the mean bug!")
    assert name == "fix-the-mean-bug" and resume is False
    episodes = root / ".ledger" / "episodes"
    _write_end(episodes / "fix-the-mean-bug", success=False)
    assert cli._auto_episode(root, "Fix the mean bug!") == \
        ("fix-the-mean-bug", True)          # failed -> resume it
    with open(episodes / "fix-the-mean-bug" / "episode.r2.jsonl", "w"):
        pass
    _write_end(episodes / "fix-the-mean-bug", success=False)  # still failed
    (episodes / "fix-the-mean-bug" / "episode.r2.jsonl").unlink()
    _write_end2 = episodes / "fix-the-mean-bug"
    # mark succeeded -> a new request gets a fresh episode
    import os
    os.remove(_write_end2 / "episode.jsonl")
    _write_end(_write_end2, success=True)
    name, resume = cli._auto_episode(root, "Fix the mean bug!")
    assert name == "fix-the-mean-bug-2" and resume is False


@needs_git
def test_chat_runner_end_to_end(repo):
    def responder(prompt):
        if "REPOSITORY FILES" in prompt:
            return FIX_DIFF
        return json.dumps({"goal": sorted(GOAL), "rationale": "green"})

    args = SimpleNamespace(yes=True, profile=None, max_author_attempts=None,
                           test=None, budget=None, max_steps=None,
                           deny=None, allow=None)
    runner = cli._chat_runner(repo, args, {"test_cmd": TEST_CMD},
                              FakeBackend(responder))
    out = runner("fix the failing test and commit")
    assert out["success"], out
    assert out["episode"] == "fix-the-failing-test-and-commit"
    assert out["resumed"] is False
    assert (repo / ".ledger" / "config.json").is_file()  # auto-initialized
    assert "succeeded" in out["summary_plain"]
    # same task again after success -> a fresh, deduped episode
    out2 = runner("fix the failing test and commit")
    assert out2["episode"] == "fix-the-failing-test-and-commit-2"


# -- TUI editor state --------------------------------------------------------

def _type(state, text):
    for ch in text:
        state.handle(f"char:{ch}")


def test_editor_completion_cycle_and_submit():
    s = tui.EditorState()
    _type(s, "/he")
    assert [c for c, _ in s.matches()] == ["/help"]
    s.handle("tab")
    assert s.buffer == "/help"
    assert s.handle("enter") == "/help"
    assert s.buffer == ""


def test_editor_completion_cycles_between_matches():
    s = tui.EditorState()
    _type(s, "/c")
    names = [c for c, _ in s.matches()]
    assert names == ["/context", "/clear"]
    s.handle("tab")
    assert s.buffer == "/context"
    s.handle("tab")
    assert s.buffer == "/clear"


def test_editor_history_and_editing():
    s = tui.EditorState()
    _type(s, "first message")
    s.handle("enter")
    _type(s, "sec")
    s.handle("up")
    assert s.buffer == "first message"
    s.handle("down")
    assert s.buffer == "sec"
    s.handle("left")
    s.handle("backspace")
    assert s.buffer == "sc"
    s.handle("esc")
    assert s.buffer == ""


def test_hint_shows_completions_or_help():
    s = tui.EditorState()
    assert "Tab completes" in s.hint()
    _type(s, "/sa")
    assert "/save" in s.hint()


def test_banner_and_spinner_offline():
    text = tui.banner("repo", "attended", "claude-cli:default")
    assert "LEDGER" in text and "attended" in text
    assert tui.interactive() is False  # pytest capture is not a TTY
    with tui.spinner("working"):       # no-op off-TTY, must not raise
        pass


# -- the framed input field --------------------------------------------------

def _box_chars():
    return ("╭", "╮", "╰", "╯", "│") if tui.ui.UNICODE \
        else ("+", "+", "+", "+", "|")


def test_frame_is_a_closed_box_of_uniform_width():
    ed = tui.LineEditor()
    _type(ed.state, "hello")
    lines, _ = ed.frame()
    tl, tr, bl, br, v = _box_chars()
    plain = [tui.ui.plain(x) for x in lines]

    assert len(plain) == 3
    assert plain[0].startswith(tl) and plain[0].endswith(tr)
    assert plain[1].startswith(v) and plain[1].endswith(v)   # closed sides
    assert plain[2].startswith(bl) and plain[2].endswith(br)
    widths = {len(p) for p in plain}
    assert len(widths) == 1, f"ragged frame: {widths}"       # square box
    assert "hello" in plain[1]
    assert "session" in plain[0]


def test_frame_cursor_column_tracks_the_caret():
    ed = tui.LineEditor()
    lines, col_empty = ed.frame()
    _type(ed.state, "abc")
    lines, col3 = ed.frame()
    assert col3 == col_empty + 3
    ed.state.handle("left")
    _, col2 = ed.frame()
    assert col2 == col3 - 1
    # the caret sits exactly on the character it precedes
    plain_mid = tui.ui.plain(lines[1])
    assert plain_mid[col3 - 1 - 1] == "c"


def test_frame_windows_long_input_to_keep_the_caret_visible():
    ed = tui.LineEditor()
    _type(ed.state, "x" * 400)
    lines, col = ed.frame()
    plain = [tui.ui.plain(x) for x in lines]
    assert len({len(p) for p in plain}) == 1     # still a square box
    assert 1 <= col <= len(plain[1])             # caret stays on screen


def test_frame_shows_completions_in_the_footer():
    ed = tui.LineEditor()
    _type(ed.state, "/to")
    lines, _ = ed.frame()
    assert "/tools" in tui.ui.plain(lines[2])


def test_frame_stays_square_with_many_completions(monkeypatch):
    """A footer listing several commands must be trimmed to the frame, not
    burst it -- the bug a visual check caught and uniform-width missed."""
    import shutil as _sh
    monkeypatch.setattr(_sh, "get_terminal_size",
                        lambda *a: os.terminal_size((60, 24)))
    ed = tui.LineEditor()
    _type(ed.state, "/t")                    # matches /tools and /tree
    lines, _ = ed.frame()
    plain = [tui.ui.plain(x) for x in lines]
    assert len({len(p) for p in plain}) == 1, [len(p) for p in plain]


def test_hint_respects_its_budget():
    s = tui.EditorState()
    _type(s, "/t")
    assert tui.ui.visible_len(s.hint(budget=12)) <= 12
    assert tui.ui.visible_len(s.hint(budget=200)) <= 200


def test_visible_len_ignores_ansi():
    assert tui.ui.visible_len(tui.ui.accent("abc")) == 3
    assert tui.ui.plain(tui.ui.bold("x")) == "x"
