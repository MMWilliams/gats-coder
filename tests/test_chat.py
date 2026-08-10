"""Chat: history rendering, journaling, and REPL command handling."""

from ledger.chat import Conversation, repl
from ledger.journal import Journal
from ledger.llm import FakeBackend


def test_ask_records_both_turns():
    conv = Conversation(backend=FakeBackend("hi there"))
    assert conv.ask("hello") == "hi there"
    assert [t.role for t in conv.turns] == ["user", "assistant"]
    assert conv.turns[0].text == "hello"


def test_history_is_rendered_into_the_prompt():
    seen = []

    def spy(prompt: str) -> str:
        seen.append(prompt)
        return "ok"

    conv = Conversation(backend=FakeBackend(spy))
    conv.ask("first question")
    conv.ask("second question")
    assert "first question" in seen[1]
    assert "CONVERSATION SO FAR" in seen[1]


def test_history_is_bounded():
    conv = Conversation(backend=FakeBackend("ok"))
    for i in range(40):
        conv.ask(f"q{i}")
    rendered = conv.render("next")
    assert "q0:" not in rendered
    assert "q39" in rendered


def test_conversation_is_journaled(tmp_path):
    path = str(tmp_path / "chat.jsonl")
    with Journal(path) as journal:
        conv = Conversation(backend=FakeBackend("an answer"), journal=journal)
        conv.ask("a question")
    entries = Journal.read(path)
    assert [e["t"] for e in entries] == ["CHAT_USER", "CHAT_ASSISTANT"]
    assert entries[1]["text"] == "an answer"
    ok, err = Journal.verify_chain(path)
    assert ok, err


def _repl(lines, conv, root):
    out = []
    it = iter(lines)
    repl(conv, root, input_fn=lambda _: next(it), output_fn=out.append)
    return out


def test_repl_exits_cleanly(tmp_path):
    conv = Conversation(backend=FakeBackend("x"))
    out = _repl(["/exit"], conv, tmp_path)
    assert any("LEDGER chat" in line for line in out)


def test_repl_clear_forgets_history(tmp_path):
    conv = Conversation(backend=FakeBackend("x"))
    conv.ask("remembered?")
    _repl(["/clear", "/exit"], conv, tmp_path)
    assert conv.turns == []


def test_repl_reports_unknown_commands(tmp_path):
    conv = Conversation(backend=FakeBackend("x"))
    out = _repl(["/nope", "/exit"], conv, tmp_path)
    assert any("unknown command" in line for line in out)


def test_repl_saves_a_transcript(tmp_path):
    conv = Conversation(backend=FakeBackend("the reply"))
    conv.ask("the question")
    target = tmp_path / "t.txt"
    _repl([f"/save {target}", "/exit"], conv, tmp_path)
    text = target.read_text(encoding="utf-8")
    assert "the question" in text and "the reply" in text


def test_repl_survives_a_backend_failure(tmp_path):
    def boom(_prompt):
        raise RuntimeError("backend exploded")

    conv = Conversation(backend=FakeBackend(boom))
    out = _repl(["anything", "/exit"], conv, tmp_path)
    assert any("backend error" in line for line in out)


def test_repl_handles_eof(tmp_path):
    def eof(_prompt):
        raise EOFError

    conv = Conversation(backend=FakeBackend("x"))
    out = []
    assert repl(conv, tmp_path, input_fn=eof, output_fn=out.append) == 0
