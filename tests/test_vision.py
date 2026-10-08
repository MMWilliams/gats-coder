"""The visual judge (SS16): making "beautiful" verifiable."""

import json
import types
from pathlib import Path

import pytest

from ledger import vision
from ledger.vision import RUBRIC, Critique, critique_image


def _reply(scores, problems=("a", "b", "c"), summary="s"):
    return json.dumps({"scores": scores, "problems": list(problems),
                       "summary": summary})


class FakeResponse:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_ollama(monkeypatch, reply):
    monkeypatch.setattr(vision.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(
                            json.dumps({"response": reply})))


def test_a_good_score_passes_and_a_bad_one_does_not(tmp_path, monkeypatch):
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG" + b"0" * 4000)
    good = {n: 8 for n, _ in RUBRIC}
    _patch_ollama(monkeypatch, _reply(good))
    assert critique_image(shot, threshold=6.5).ok is True

    bad = {n: 3 for n, _ in RUBRIC}
    _patch_ollama(monkeypatch, _reply(bad))
    c = critique_image(shot, threshold=6.5)
    assert c.ok is False and c.mean == 3.0


def test_an_unreachable_model_is_not_assessed_rather_than_failed(
        tmp_path, monkeypatch):
    """A machine with no vision model must still be able to build things.
    'Not assessed' and 'ugly' are different states."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG" + b"0" * 4000)

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(vision.urllib.request, "urlopen", boom)
    c = critique_image(shot)
    assert c.assessed is False and c.ok is False and c.error


def test_junk_from_the_model_does_not_raise(tmp_path, monkeypatch):
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG" + b"0" * 4000)
    _patch_ollama(monkeypatch, "I think it looks quite nice actually")
    assert critique_image(shot).assessed is False


def test_scores_are_clamped(tmp_path, monkeypatch):
    """A model that returns 400 for lighting must not drag the mean up."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG" + b"0" * 4000)
    _patch_ollama(monkeypatch, _reply({n: 400 for n, _ in RUBRIC}))
    assert critique_image(shot).mean == 10.0


def test_a_missing_screenshot_is_reported(tmp_path):
    assert critique_image(tmp_path / "nope.png").assessed is False


def test_the_critique_becomes_actionable_evidence():
    """The score gates; the complaints are what actually get fixed, so they
    have to reach the authoring prompt intact."""
    c = Critique(scores={"lighting": 2}, assessed=True,
                 problems=["the scene has one ambient light so every "
                           "surface is the same brightness"],
                 summary="flat")
    text = c.as_evidence()
    assert "ambient light" in text and "lighting: 2/10" in text


def test_an_unassessed_critique_contributes_no_evidence():
    assert Critique(error="no model").as_evidence() == ""


def test_design_ok_never_substitutes_for_tests_passing():
    """A model's opinion is not the same kind of evidence as an executed
    test. Something beautiful that fails its tests has not passed."""
    from ledger.skills_llm import review_design_action
    a = review_design_action()
    assert a.add == frozenset({"design.ok"})
    assert not any("tests.pass" in p for p in a.add)
    # Stochastic: whether a build passes review cannot be promised. But the
    # effects are DECLARED -- marked unknown, the planner never reaches for
    # the review at all (see the reachability test below).
    assert a.deterministic is False and a.effects_known is True


def test_a_goal_that_needs_design_ok_actually_reaches_the_review(tmp_path,
                                                                 monkeypatch):
    """Regression: with the review's effects marked unknown, an episode
    whose goal required `design.ok` never planned the review. It authored
    patches until the budget ran out and escalated `no_path`, one
    applicable action away from the proposition it needed."""
    import json
    import subprocess
    import sys

    from conftest import BUGGY, GOAL, TEST_CMD
    from ledger import vision
    from ledger.executor import run_repo_episode
    from ledger.journal import Journal
    from ledger.llm import FakeBackend

    monkeypatch.setattr(vision, "screenshot",
                        lambda root, out, **k: bool(
                            Path(out).write_bytes(b"x" * 2000) or True))
    monkeypatch.setattr(vision, "critique_image", lambda p, **k: vision.Critique(
        scores={"lighting": 8}, problems=["flat"], summary="s", ok=True,
        assessed=True))
    monkeypatch.setattr(vision, "compare", lambda before, after, **k:
                        vision.Comparison(better="after", agreed=True,
                                          why="brighter"))

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, capture_output=True)

    git("init", "-q")
    (repo / "calc.py").write_text(BUGGY, encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8")
    (repo / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")

    episode = tmp_path / "ep"
    episode.mkdir(parents=True)
    (episode / "design-baseline.png").write_bytes(b"x" * 2000)

    def responder(prompt):
        if "REPOSITORY FILES" in prompt:
            return ("FILE: calc.py\n```python\ndef add(a, b):\n"
                    "    return a + b\n```")
        return json.dumps({"goal": sorted(GOAL), "rationale": "green"})

    result, _, journal = run_repo_episode(
        repo, episode, None, TEST_CMD, task_text="build a game",
        backend=FakeBackend(responder), slots=["it1"],
        required_patch_slot="it1", product_kind="none", product_review=False,
        design_review=True, max_author_attempts=4)

    entries = Journal.read(str(journal))
    assert "design.ok" in entries[0]["goal"]
    assert any(e.get("t") == "EXEC" and e.get("action") == "ReviewDesign"
               for e in entries), "the planner never reached the review"
    assert result.success


def test_the_sensor_reads_the_cache_and_never_guesses():
    from ledger.sensors import ExpensiveResultCache, design_sensor
    cache = ExpensiveResultCache()
    sensor = design_sensor(cache, lambda root: "fp")
    assert sensor.probe(Path(".")) == set()      # miss -> silence, not False
    cache.put("fp", {"design.ok"})
    assert sensor.probe(Path(".")) == {"design.ok"}


def test_a_missing_page_is_not_photographed(tmp_path):
    """Serving a directory with no index.html photographs a directory
    listing, which the judge would then score as a bad UI. Absence must be
    reported as absence."""
    from ledger.vision import screenshot
    assert screenshot(tmp_path, tmp_path / "out.png") is False


def test_the_server_is_waited_for_not_slept_on():
    """A fixed sleep was enough on an idle machine and not enough while the
    episode ran its own suite, so reviews failed intermittently for reasons
    unrelated to the build."""
    import inspect

    from ledger import vision
    src = inspect.getsource(vision.screenshot)
    assert "create_connection" in src, "still sleeping instead of polling"
    assert "sys.executable" in src, (
        "spawning bare 'python' -- an episode need not have one on PATH")


def test_the_pairwise_judge_is_asked_in_both_orders(tmp_path, monkeypatch):
    """Position bias is the standard failure of a pairwise judge, and it is
    not hypothetical: measured, qwen3-vl:32b and qwen2.5vl:7b answered "the
    second image" every single time, INCLUDING when both images were
    identical. Without the swap this looks like a perfect judge on any test
    where the improved version happens to come second."""
    from ledger import vision

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for p in (a, b):
        p.write_bytes(b"\x89PNG" + b"0" * 4000)

    asked = []

    def always_second(first, second, model, timeout):
        asked.append((first.name, second.name))
        return "B"

    monkeypatch.setattr(vision, "_ask_pair", always_second)
    verdict = vision.compare(a, b)
    assert len(asked) == 2, "the pair must be judged in both orders"
    assert asked[0] != asked[1], "the second question must swap the images"
    assert verdict.better == "same" and not verdict.agreed, (
        "a judge that always says 'the second one' must yield no verdict")


def test_agreement_in_both_orders_is_a_real_verdict(tmp_path, monkeypatch):
    from ledger import vision

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for p in (a, b):
        p.write_bytes(b"\x89PNG" + b"0" * 4000)

    def prefers_b_by_content(first, second, model, timeout):
        # picks whichever argument is b.png, wherever it sits
        return "A" if first.name == "b.png" else "B"

    monkeypatch.setattr(vision, "_ask_pair", prefers_b_by_content)
    verdict = vision.compare(a, b)
    assert verdict.better == "after" and verdict.agreed


def test_the_design_gate_is_pairwise_not_an_absolute_score():
    """Absolute scores from every local model measured were unusable:
    compressed for one, and INVERTED for gemma4, which rated the better
    build 2.0 and the worse one 2.2. Any fixed threshold over that is
    either unreachable or meaningless."""
    import inspect

    from ledger.executor import ReviewDesignSkill
    src = inspect.getsource(ReviewDesignSkill.run)
    assert "compare(" in src, "the gate is not using pairwise comparison"
    assert "verdict.agreed" in src, "the order-swap agreement is not required"


def test_the_first_review_captures_a_baseline_and_claims_nothing():
    import inspect

    from ledger.executor import ReviewDesignSkill
    src = inspect.getsource(ReviewDesignSkill.run)
    assert "captured the starting appearance" in src, (
        "the first review must record a baseline rather than pass or fail")
    assert "self.baseline is None or not self.baseline.is_file()" in src
