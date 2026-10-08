"""The session remembers, and its work comes home.

Two ways a chat session lost track of itself: the transcript was cut to the
last twelve turns, and a successful episode's work stayed on its own branch
in its own worktree, so the next question about "the game" was answered
from a repository with nothing in it.
"""

import json
from pathlib import Path

from conftest import FIXED, GOAL, TEST_CMD, needs_git

from ledger import cli
from ledger.chat import Conversation, Turn, repl
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.repo import fast_forward, git


def _router(reply="ok"):
    def respond(prompt):
        if "route messages" in prompt:
            return json.dumps({"intent": "change", "task": "fix the bug in calc.py"})
        if "Summarize the earlier part" in prompt:
            return "MODEL SUMMARY of the early turns"
        return reply
    return FakeBackend(respond)


# ------------------------------------------------------------ the window

def test_the_whole_transcript_is_fed_while_it_fits():
    conv = Conversation(backend=FakeBackend("reply"))
    for i in range(30):
        conv.turns.append(Turn("user", f"question {i}"))
        conv.turns.append(Turn("assistant", f"answer {i}"))
    prompt = conv.render("next")
    assert "question 0" in prompt and "answer 29" in prompt
    assert "summarized" not in prompt


def test_old_turns_fold_into_a_journaled_summary_only_when_over_budget(tmp_path):
    journal = Journal(str(tmp_path / "chat.jsonl"))
    conv = Conversation(backend=_router("reply"), journal=journal,
                        budget_chars=1200)
    for i in range(12):
        conv.turns.append(Turn("user", f"request {i} " + "x" * 80))
        conv.turns.append(Turn("assistant",
                               f"Episode 'e{i}' succeeded: RunSuite" if i == 3
                               else f"answer {i} " + "y" * 80))
    with journal:
        conv.ask("latest question")
    assert "MODEL SUMMARY" in conv.summary
    assert "Episode 'e3' succeeded" in conv.summary     # facts survive verbatim
    assert conv.turns[-2].text == "latest question"
    assert "request 11" in conv.render("more")          # recent turns intact
    assert "request 0" not in conv.render("more").split("CONVERSATION SO FAR")[1]
    kinds = [e["t"] for e in Journal.read(str(tmp_path / "chat.jsonl"))]
    assert "CHAT_SUMMARY" in kinds


def test_a_broken_summarizer_still_keeps_a_digest():
    class Broken(FakeBackend):
        def complete(self, prompt):
            if "Summarize" in prompt:
                raise RuntimeError("down")
            return "reply"
    conv = Conversation(backend=Broken("x"), budget_chars=300)
    for i in range(6):
        conv.turns.append(Turn("user", f"ask {i} " + "z" * 60))
        conv.turns.append(Turn("assistant", f"say {i} " + "z" * 60))
    conv.ask("q")
    assert conv.summary.startswith("- User: ask 0")


# ------------------------------------------------------- coming home

def _outcome(merge, branch="ledger/e"):
    return {"success": True, "plan": ["RunSuite"], "steps": 1,
            "incidents": 0, "escalation": None, "journal": "j.jsonl",
            "episode": "e", "resumed": False, "branch": branch,
            "worktree": "C:/wt", "merge": merge, "merge_auto": False,
            "summary_plain": "Episode 'e' succeeded", "summary_styled": "ok"}


def test_a_successful_episode_is_offered_back_into_the_repository(tmp_path):
    merges, outputs = [], []

    def merge():
        merges.append(1)
        return True, "fast-forwarded main to ledger/e"
    conv = Conversation(backend=_router())
    inputs = iter(["please fix the bug", "", "", "/exit"])   # yes, yes
    repl(conv, tmp_path, input_fn=lambda p: next(inputs),
         output_fn=outputs.append,
         runner=lambda t, progress=None: _outcome(merge))
    assert merges == [1]
    assert any("Merged into the repository" in o for o in outputs)
    assert "fast-forwarded main" in conv.turns[-1].text


def test_declining_the_merge_still_tells_the_session_where_the_work_is(tmp_path):
    outputs = []
    conv = Conversation(backend=_router())
    inputs = iter(["please fix the bug", "", "n", "/exit"])
    repl(conv, tmp_path, input_fn=lambda p: next(inputs),
         output_fn=outputs.append,
         runner=lambda t, progress=None: _outcome(
             lambda: (_ for _ in ()).throw(AssertionError("must not merge"))))
    assert any("Not merged (declined)" in o and "ledger/e" in o for o in outputs)
    assert "C:/wt" in conv.turns[-1].text


def test_fast_forward_refuses_a_detached_head(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    (root / "a").write_text("a", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "a")
    git(root, "checkout", "-q", "--detach")
    ok, why = fast_forward(root, "ledger/x")
    assert ok is False and "detached" in why


@needs_git
def test_the_chat_runner_brings_verified_work_into_the_repository(repo):
    from types import SimpleNamespace

    def responder(prompt):
        if "REPOSITORY FILES" in prompt:
            return ("FILE: calc.py\n```python\ndef add(a, b):\n"
                    "    return a + b\n```")
        return json.dumps({"goal": sorted(GOAL), "rationale": "green"})

    args = SimpleNamespace(yes=True, profile=None, max_author_attempts=None,
                           test=None, budget=None, max_steps=None,
                           deny=None, allow=None)
    runner = cli._chat_runner(repo, args, {"test_cmd": TEST_CMD},
                              FakeBackend(responder))
    out = runner("fix the failing test and commit")
    assert out["success"], out
    assert out["branch"] == "ledger/fix-the-failing-test-and-commit"
    assert "Its work is on branch" in out["summary_plain"]
    assert (repo / "calc.py").read_text(encoding="utf-8") != FIXED  # not yet
    ok, detail = out["merge"]()
    assert ok, detail
    assert (repo / "calc.py").read_text(encoding="utf-8") == FIXED
    assert git(repo, "rev-parse", "HEAD").stdout == git(
        Path(out["worktree"]), "rev-parse", "HEAD").stdout
