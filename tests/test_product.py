"""The usability pass (SS15).

Every product failure in this project's history passed its tests: a chess
game with no opponent, a scene that rendered black, a car whose steering
input was stored and never read. This is the layer that says so first.
"""

import json
import types

import pytest

from ledger.llm import FakeBackend
from ledger.product import (CHECKLISTS, Concern, detect_kind, prompt_block,
                            requirements, review)


# ------------------------------------------------------------- detection

@pytest.mark.parametrize("task,expected", [
    # The one that was missed: unmistakably a game, and it contains none of
    # game / player / level / score.
    ("roll the ball down a platform and avoid falling through the holes",
     "game"),
    ("race car sim against ten autonomous cars in three js", "game"),
    ("a 3d chess game with rules and a win condition", "game"),
    ("a react dashboard with a form", "web"),
    ("add a --verbose flag to the cli", "cli"),
    ("a REST endpoint for signup", "service"),
    ("a python library for parsing logs", "library"),
])
def test_the_kind_is_detected_from_the_task(task, expected):
    assert detect_kind(task) == expected


@pytest.mark.parametrize("task", [
    # None of these are product work, and a checklist forced onto them is
    # noise that teaches people to turn the feature off.
    "refactor the payment module to reduce duplication",
    "fix the race condition in the queue consumer",
    "our platform team needs a deploy script",
    "bump the dependency and re-run the tests",
])
def test_ordinary_work_is_not_forced_into_a_checklist(task):
    assert detect_kind(task) == ""


# ------------------------------------------------------------ the prompt

def test_the_gameplay_requirements_reach_the_author():
    """Cheaper than any review: in front of the model while it writes."""
    block = prompt_block("game")
    assert "controls" in block and "challenge" in block
    assert "nothing reads" in block, (
        "the specific failure -- an input stored and never read -- should be "
        "named, not implied")


def test_requirements_can_be_narrowed_by_config():
    only = requirements("game", ["controls", "outcome"])
    assert [d for d, _ in only] == ["controls", "outcome"]


def test_an_unknown_kind_contributes_nothing():
    assert prompt_block("") == ""
    assert prompt_block("spaceship") == ""
    assert requirements("") == []


def test_every_checklist_is_well_formed():
    for kind, (label, items) in CHECKLISTS.items():
        assert label and items, kind
        for dimension, text in items:
            assert dimension.islower() and " " not in dimension
            assert len(text) > 40, f"{kind}.{dimension} is too vague to act on"


# ------------------------------------------------------------- the review

def _reply(*gaps):
    return json.dumps({"gaps": list(gaps)})


def test_a_real_gap_is_reported():
    backend = FakeBackend(_reply({
        "dimension": "challenge",
        "concern": "nothing opposes the player, so you move for both sides",
        "check": "assert the opponent moves without player input"}))
    found = review("build a chess game", "tests.pass:unit", "game", backend)
    assert len(found) == 1
    assert found[0].dimension == "challenge"
    assert "both sides" in found[0].concern


def test_a_gap_outside_the_checklist_is_dropped():
    """The model must not be able to widen its own remit -- otherwise the
    review becomes a source of arbitrary opinion attached to a build."""
    backend = FakeBackend(_reply(
        {"dimension": "monetisation", "concern": "no in-app purchases",
         "check": "add a store"}))
    assert review("build a game", "c", "game", backend) == []


def test_a_broken_review_never_takes_the_episode_with_it():
    """Advice that can break a build is worse than no advice."""
    class Dead:
        id = "dead"

        def complete(self, prompt):
            raise RuntimeError("no route to host")

    assert review("t", "c", "game", Dead()) == []
    assert review("t", "c", "game", FakeBackend("not json at all")) == []
    assert review("t", "c", "game", None) == []


def test_no_kind_means_no_review():
    calls = []

    class Counting(FakeBackend):
        def complete(self, prompt):
            calls.append(prompt)
            return _reply()

    assert review("t", "c", "", Counting("x")) == []
    assert not calls, "a task with no product kind must not cost a model call"


# ------------------------------------------------------------ the wiring

def test_config_resolves_kind_review_and_require(tmp_path, monkeypatch):
    from ledger.cli import _product_options

    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))
    args = types.SimpleNamespace(product_kind=None, product_review=True,
                                 task="build a racing game in three js")
    # auto-detection: the common case needs no config at all
    assert _product_options(args, {})["product_kind"] == "game"

    # config narrows it
    cfg = {"product": {"kind": "web", "require": ["input"], "review": False}}
    got = _product_options(args, cfg)
    assert got == {"product_kind": "web", "product_require": ["input"],
                   "product_review": False}

    # an explicit flag beats config
    args.product_kind = "cli"
    assert _product_options(args, cfg)["product_kind"] == "cli"

    # and "none" turns it off entirely
    args.product_kind = "none"
    assert _product_options(args, cfg)["product_kind"] == ""


def test_the_author_prompt_carries_the_requirements(tmp_path):
    from ledger.skills_llm import AuthorPatchSkill

    (tmp_path / "main.js").write_text("// hi", encoding="utf-8")
    skill = AuthorPatchSkill("a", FakeBackend("x"), "build a game",
                             product_kind="game")
    ctx = types.SimpleNamespace(worktree=tmp_path, suite_evidence={},
                                say=lambda *a: None)
    text = skill._prompt(ctx)
    assert "ACTUALLY USABLE" in text and "controls" in text
    assert "nothing reads" in text


def test_without_a_kind_the_prompt_is_unchanged(tmp_path):
    """Ordinary work must not get a checklist bolted onto its prompt."""
    from ledger.skills_llm import AuthorPatchSkill

    (tmp_path / "main.js").write_text("// hi", encoding="utf-8")
    skill = AuthorPatchSkill("a", FakeBackend("x"), "refactor the parser")
    ctx = types.SimpleNamespace(worktree=tmp_path, suite_evidence={},
                                say=lambda *a: None)
    assert "ACTUALLY USABLE" not in skill._prompt(ctx)


def test_model_text_cannot_kill_the_command_on_a_narrow_console(monkeypatch):
    """The glyph constants degrade to ASCII, but model output can hold
    anything. Printing a character the console cannot encode raises
    UnicodeEncodeError and takes the run with it -- and on Windows a
    redirected stdout is cp1252, which has no check mark and no emoji."""
    import io

    from ledger import ui

    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252",
                              errors="strict")
    monkeypatch.setattr(ui.sys, "stdout", narrow)
    safe = ui.console_safe("verdict ✓ and a rocket \U0001f680")
    safe.encode("cp1252")          # must not raise
    assert "✓" not in safe


def test_plain_leaves_encodable_text_untouched(monkeypatch):
    import io

    from ledger import ui

    wide = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(ui.sys, "stdout", wide)
    assert ui.console_safe("verdict ✓") == "verdict ✓"
