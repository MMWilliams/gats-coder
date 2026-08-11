"""UI layer: terminal styling degrades to plain text off-TTY, and the
`ledger vis` HTML report is complete, escaped, and self-contained."""

from conftest import GOAL, TEST_CMD, needs_git, queue_fix

import ledger.cli as cli
from ledger import ui
from ledger.executor import PolicyGate, run_repo_episode
from ledger.vis import render, render_file


# -- terminal styling --------------------------------------------------------

def test_style_is_identity_without_a_tty():
    # pytest's capture is not a TTY, so color must be off: piped output and
    # CI logs read exactly as plain text.
    assert ui.COLOR is False
    assert ui.style("hello", "red", "bold") == "hello"
    assert ui.ok("fine") == "fine"


def test_plan_chain_plain():
    out = ui.plan_chain(["RunSuite", "AuthorPatch:a", "Commit"])
    assert "RunSuite" in out and "AuthorPatch" in out and ":a" in out
    assert ui.ARROW in out
    assert ui.plan_chain([]) == "(empty)"


def test_table_alignment_plain():
    out = ui.table([["RunSuite", "10", "1.000"], ["Commit", "3", "0.500"]],
                   header=["arm", "N", "Q"])
    lines = out.splitlines()
    assert len(lines) == 3
    assert lines[1].index("10") == lines[2].index("3")  # columns align


def test_headline_and_next_steps():
    assert "reached" in ui.headline(True, "reached", "nope")
    steps = ui.next_steps("ledger status x", "ledger why x 0")
    assert "next:" in steps and "ledger status x" in steps


# -- vis ---------------------------------------------------------------------

@needs_git
def test_vis_renders_successful_episode(repo, tmp_path, capsys):
    episode_dir = tmp_path / "ep"
    queue_fix(episode_dir)
    _, _, journal_path = run_repo_episode(repo, episode_dir, GOAL, TEST_CMD)

    rc = cli.main(["vis", str(journal_path)])
    out_path = journal_path.with_suffix(".html")
    assert rc == 0
    assert out_path.exists()
    html_text = out_path.read_text(encoding="utf-8")
    assert html_text.startswith("<!DOCTYPE html>")
    assert "SUCCESS" in html_text
    assert "chain intact" in html_text
    assert "RunSuite" in html_text and "Commit" in html_text
    assert "decision table" in html_text
    assert "EXEC OK" in html_text
    assert "wrote" in capsys.readouterr().out


@needs_git
def test_vis_shows_denials_and_escalation(repo, tmp_path):
    episode_dir = tmp_path / "denied"
    queue_fix(episode_dir)
    _, _, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD,
        gate=PolicyGate("autonomous",
                        rules=[{"action": "Commit", "decision": "deny",
                                "reason": "no commits today"}]),
        profile="autonomous")
    html_text = render_file(journal_path)
    assert "GOAL NOT REACHED" in html_text
    assert "GATE DENIED" in html_text
    assert "no commits today" in html_text
    assert "Escalated to the human" in html_text


def test_vis_escapes_untrusted_text():
    entries = [
        {"i": 0, "t": "HEADER", "task_text": "<script>alert(1)</script>",
         "goal": ["tests.pass:unit"], "version": "x"},
        {"i": 1, "t": "PLAN", "step": 0, "chosen": "<img src=x>",
         "state": "s", "pred_state": "s2", "wm": {"layer": "L1", "p": 1.0},
         "table": [{"a": "<img src=x>", "n": 1, "q": 1.0, "p": 1.0,
                    "layer": "L1"}], "search_iters": 1,
         "llm_planning_calls": 0},
        {"i": 2, "t": "END", "success": True, "plan": ["<img src=x>"],
         "steps": 1, "llm_planning_calls": 0},
    ]
    html_text = render(entries, True, None, "j.jsonl")
    assert "<script>" not in html_text
    assert "<img" not in html_text
    assert "&lt;script&gt;" in html_text
