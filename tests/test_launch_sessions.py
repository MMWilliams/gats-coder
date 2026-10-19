"""Launching like `claude`: bare `ledger` is the session, sessions persist
and resume, and replies render without changing what was said."""
import json

import pytest

from ledger import cli, sessions, tui, ui
from ledger.llm import FakeBackend


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def _model(monkeypatch, seen):
    def answer(prompt):
        seen.append(prompt)
        return f"answer {len(seen)}"
    monkeypatch.setattr(cli, "_backend",
                        lambda args, cfg=None: FakeBackend(answer, id="fake:m"))


def test_bare_flags_open_the_session(home, project, monkeypatch, capsys):
    seen = []
    _model(monkeypatch, seen)
    (home / ".ledger").mkdir()
    (home / ".ledger" / "config.json").write_text('{"backend": {"id": "fake"}}')
    assert cli.main(["-p", "what is this repo?"]) == 0
    assert "answer 1" in capsys.readouterr().out
    assert "what is this repo?" in seen[0]


def test_a_session_is_saved_outside_the_repo_and_continues(home, project,
                                                           monkeypatch, capsys):
    seen = []
    _model(monkeypatch, seen)
    cli.main(["chat", "-p", "remember the word heliotrope"])
    saved = sessions.listing(project)
    assert len(saved) == 1 and "heliotrope" in saved[0].title
    assert str(saved[0].path).startswith(str(home))
    assert not (project / ".ledger").exists()
    cli.main(["chat", "-c", "-p", "which word?"])
    assert "heliotrope" in seen[-1], "the continued session must carry history"
    assert len(sessions.listing(project)) == 1


def test_resume_by_number_id_or_short_tag(home, project, monkeypatch):
    seen = []
    _model(monkeypatch, seen)
    cli.main(["chat", "-p", "first session"])
    cli.main(["chat", "-p", "second session"])
    newest, oldest = sessions.listing(project)
    assert sessions.find(project, "2").id == oldest.id
    assert sessions.find(project, oldest.id[-4:]).id == oldest.id
    cli.main(["chat", "--resume", oldest.id, "-p", "back again"])
    assert "first session" in seen[-1] and "second session" not in seen[-1]


def test_resuming_a_missing_session_fails_cleanly(home, project, monkeypatch, capsys):
    _model(monkeypatch, [])
    assert cli.main(["chat", "--resume", "nope", "-p", "x"]) == 1


def test_episode_results_survive_a_resume(home, project):
    from ledger.chat import Conversation
    from ledger.journal import Journal
    path = sessions.new_path(project)
    j = Journal(str(path))
    conv = Conversation(backend=FakeBackend("x"), journal=j)
    conv.record_episode("fix the bug", "fix the bug", {"success": True,
                                                      "plan": ["RunSuite"]},
                        "Episode 'fix' succeeded")
    j.close()
    fresh = Conversation(backend=FakeBackend("x"))
    assert sessions.restore(fresh, path) == 1
    assert fresh.turns[-1].text == "Episode 'fix' succeeded"


def test_first_message_on_the_command_line_starts_the_work(monkeypatch, capsys):
    reads = iter(["/exit"])
    fn = cli._first_then("fix the login bug", lambda p="> ": next(reads))
    assert fn() == "fix the login bug"
    assert fn() == "/exit"


def test_render_is_identity_without_colour():
    text = "## Title\n- item `x`\n```python\ndef f(): pass\n```"
    assert tui.render(text) == text


def test_render_highlights_code_and_diffs_without_changing_the_text(monkeypatch):
    monkeypatch.setattr(ui, "COLOR", True)
    text = ("```python\ndef f(x):\n    return 'a' + 1  # note\n```\n"
            "```diff\n-old line\n+new line\n```")
    out = tui.render(text)
    assert "\x1b[" in out
    for piece in ("def", "f", "'a'", "# note", "-old line", "+new line"):
        assert piece in ui.plain(out)


def test_the_banner_names_the_danger(monkeypatch):
    text = tui.banner(repo="r", profile="attended", backend_id="fake:m", skip=True)
    assert "PERMISSIONS OFF" in text and "bypassed" in text


def test_panels_stay_square_with_styled_content(monkeypatch):
    monkeypatch.setattr(ui, "COLOR", True)
    box = ui.panel("t", [ui.ok("styled"), "plain " * 30], width=40)
    widths = {ui.visible_len(line) for line in box.split("\n")}
    assert widths == {40}


def test_chat_help_lists_resume_and_skip_flags():
    parser = cli.build_parser()
    chat = parser._subparsers._group_actions[0].choices["chat"]
    flags = {f for a in chat._actions for f in a.option_strings}
    for flag in ("-c", "--continue", "--resume", "-r", "-p",
                 "--dangerously-skip-permissions"):
        assert flag in flags


def test_sessions_list_newest_first_and_skip_empty_ones(home, project):
    sessions.new_path(project)                     # empty: never listed
    p = sessions.new_path(project)
    p.write_text(json.dumps({"t": "CHAT_USER", "text": "hello"}) + "\n")
    assert [s.title for s in sessions.listing(project)] == ["hello"]
