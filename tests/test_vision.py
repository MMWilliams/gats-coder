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
    # unknown effects: whether a build passes review cannot be promised
    assert a.effects_known is False and a.deterministic is False


def test_the_sensor_reads_the_cache_and_never_guesses():
    from ledger.sensors import ExpensiveResultCache, design_sensor
    cache = ExpensiveResultCache()
    sensor = design_sensor(cache, lambda root: "fp")
    assert sensor.probe(Path(".")) == set()      # miss -> silence, not False
    cache.put("fp", {"design.ok"})
    assert sensor.probe(Path(".")) == {"design.ok"}
