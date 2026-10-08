"""Deciding what to build, before building it (SS17).

A one-sentence request reached the author as one sentence, and the author
filled the silence with the smallest thing that could pass: a chess game of
untextured primitives with a test API and no way to click. This is the step
that interrogates the request first, freezes the answer, and holds the
build to it.
"""

import json

from conftest import GOAL, TEST_CMD, needs_git

from ledger.brief import (Brief, Requirement, Verdict, ask, answer, check,
                          load_brief, source_files, unmet_block, write_brief)
from ledger.executor import ReviewBriefSkill, run_repo_episode
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.sensors import ExpensiveResultCache, brief_sensor
from ledger.skills_llm import build_authoring_catalog, review_brief_action


QUESTIONS = ["What does the player see in the first five seconds?",
             "How does a player make a move with the mouse?",
             "What is shown when the game ends?"]
REQUIREMENTS = [
    {"id": "Pieces Are Modelled", "text": "every piece is a distinct shape",
     "check": "six different geometries in the scene"},
    {"id": "click to move", "text": "a piece moves by clicking the board",
     "check": "a pointer handler raycasts to a square"},
]


def _brief_backend(met=True, verdict_ids=("pieces-are-modelled",
                                          "click-to-move")):
    def respond(prompt):
        if "Ask between" in prompt:
            return json.dumps({"questions": QUESTIONS})
        if "turn your answers into requirements" in prompt:
            return json.dumps({
                "answers": [{"question": QUESTIONS[0], "answer": "a lit board"}],
                "requirements": REQUIREMENTS})
        if "decide from the source alone" in prompt:
            return json.dumps({"verdicts": [
                {"id": i, "met": met, "evidence": f"{i}: src/main.js:12"}
                for i in verdict_ids]})
        return "{}"
    return FakeBackend(respond)


def _brief(*requirements):
    return Brief(task="build a 3d chess game", kind="game",
                 questions=tuple(QUESTIONS), answers=(),
                 requirements=requirements or (
                     Requirement("click-to-move", "a piece moves by clicking",
                                 "a pointer handler raycasts"),))


# --------------------------------------------------- asking and answering

def test_it_asks_about_the_deliverable_and_turns_answers_into_requirements():
    backend = _brief_backend()
    questions = ask("build a 3d chess game", "game", backend)
    assert questions == QUESTIONS
    answers, requirements = answer("build a 3d chess game", questions, backend)
    assert answers[0][1] == "a lit board"
    # ids are slugged so they can be referred to later, and stay stable
    assert [r.id for r in requirements] == ["pieces-are-modelled",
                                            "click-to-move"]
    assert requirements[1].check == "a pointer handler raycasts to a square"


def test_the_question_prompt_carries_the_kinds_own_dimensions():
    seen = []

    class Watch(FakeBackend):
        def complete(self, prompt):
            seen.append(prompt)
            return json.dumps({"questions": QUESTIONS})

    ask("build a game", "game", Watch("x"))
    assert "controls" in seen[0] and "first_5_seconds" in seen[0]
    assert "ask past them rather than about them" in seen[0]


def test_a_broken_model_yields_no_brief_rather_than_a_crash(tmp_path):
    class Broken(FakeBackend):
        def complete(self, prompt):
            raise RuntimeError("backend down")

    assert ask("t", "game", Broken("x")) == []
    assert write_brief("t", "game", Broken("x"), tmp_path / "b.json") is None
    assert not (tmp_path / "b.json").exists()
    assert load_brief(tmp_path / "missing.json") is None


# ------------------------------------------------------------ freezing it

def test_the_brief_is_written_once_hashed_and_reloadable(tmp_path):
    path = tmp_path / "brief.json"
    written = write_brief("build a 3d chess game", "game",
                          _brief_backend(), path)
    assert written is not None
    reloaded = load_brief(path)
    assert reloaded == written
    assert reloaded.digest() == written.digest()
    # the hash covers the bar, not the conversation that produced it
    moved = Brief(task=written.task, kind=written.kind, questions=(),
                  answers=(), requirements=written.requirements)
    assert moved.digest() == written.digest()
    weaker = Brief(task=written.task, kind=written.kind, questions=(),
                   answers=(), requirements=written.requirements[:1])
    assert weaker.digest() != written.digest()


def test_requirements_reach_the_authoring_prompt():
    prompt = _brief().as_prompt()
    assert "WHAT THIS BUILD MUST BE" in prompt
    assert "click-to-move: a piece moves by clicking" in prompt
    assert "how to tell: a pointer handler raycasts" in prompt


# --------------------------------------------------------------- judging

def test_unaddressed_requirements_are_unmet_so_silence_cannot_pass_a_build(
        tmp_path):
    (tmp_path / "main.js").write_text("// nothing here", encoding="utf-8")
    from ledger.repo import git
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    brief = _brief(Requirement("a", "thing one", ""),
                   Requirement("b", "thing two", ""))
    verdicts = check(brief, tmp_path,
                     _brief_backend(verdict_ids=("a",)))          # only "a"
    assert {v.id: v.met for v in verdicts} == {"a": True, "b": False}
    assert "not addressed by the review" in next(
        v.evidence for v in verdicts if v.id == "b")


def test_a_verdict_about_an_invented_requirement_is_discarded(tmp_path):
    from ledger.repo import git
    (tmp_path / "main.js").write_text("code", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    brief = _brief(Requirement("a", "thing one", ""))
    verdicts = check(brief, tmp_path,
                     _brief_backend(verdict_ids=("a", "invented")))
    assert [v.id for v in verdicts] == ["a"]


def test_nothing_to_read_is_not_assessed_rather_than_failed(tmp_path):
    from ledger.repo import git
    git(tmp_path, "init", "-q")
    assert check(_brief(), tmp_path, _brief_backend()) is None


def test_the_judge_reads_the_source_even_when_it_shares_no_words_with_the_brief(
        tmp_path):
    """Ranking by overlap alone handed the judge nothing for a brief whose
    wording differed from the code's, so `brief.met` could never be
    reached however good the build was."""
    from ledger.repo import git
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "scene.js").write_text("export const r = 1;",
                                               encoding="utf-8")
    (tmp_path / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A", "-f")
    brief = _brief(Requirement("shapes", "every piece is a distinct shape", ""))

    files = source_files(brief, tmp_path)
    assert "index.html" in files and "src/scene.js" in files
    assert not any("node_modules" in f for f in files)
    verdicts = check(brief, tmp_path, _brief_backend(verdict_ids=("shapes",)))
    assert [v.id for v in verdicts] == ["shapes"]


def test_unmet_findings_become_evidence_and_met_ones_are_silent():
    assert unmet_block([Verdict("a", True, "src:1")]) == ""
    block = unmet_block([Verdict("a", True, "src:1"),
                         Verdict("b", False, "no result screen anywhere")])
    assert "b: no result screen anywhere" in block
    assert "a:" not in block.split("NOT met yet:")[1].split("\n")[1]


# -------------------------------------------------------- the contract

def test_the_brief_verdict_is_a_judge_and_never_replaces_the_suite():
    action = review_brief_action()
    assert action.add == frozenset({"brief.met"})
    assert action.prec == frozenset({"tests.pass:unit"})
    assert not any("tests.pass" in p for p in action.add)
    # Optimistic contract, refuted per state -- RunSuite's shape. Marked
    # effects-unknown instead, the planner never reaches for it: L1 cannot
    # predict that this is the action that delivers the proposition.
    assert action.deterministic is False and action.effects_known is True


def test_a_patch_invalidates_the_brief_verdict():
    apply = next(a for a in build_authoring_catalog(["a"])
                 if a.name == "ApplyPatch:a")
    assert "brief.met" in apply.delete


def test_the_sensor_reads_the_cache_and_never_guesses():
    cache = ExpensiveResultCache()
    sensor = brief_sensor(cache, lambda root: "fp")
    from pathlib import Path
    assert sensor.probe(Path(".")) == set()
    cache.put("fp", {"brief.met"})
    assert sensor.probe(Path(".")) == {"brief.met"}


# ------------------------------------------------------------ the skill

def _ctx(repo, tmp_path):
    from ledger.executor import BlobStore, ExecContext
    queue = tmp_path / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    return ExecContext(worktree=repo, blobs=BlobStore(tmp_path / "blobs"),
                       test_cache=ExpensiveResultCache(), queue_dir=queue,
                       applied_dir=tmp_path / "applied")


@needs_git
def test_the_review_names_what_is_missing_then_passes(repo, tmp_path):
    path = tmp_path / "brief.json"
    write_brief("fix the calculator", "library", _brief_backend(), path)
    ctx = _ctx(repo, tmp_path)
    cache = ExpensiveResultCache()

    failing = ReviewBriefSkill(cache, _brief_backend(met=False), path)
    result = failing.run(ctx)
    assert not result.ok and result.claimed_props == frozenset()
    assert "2 of 2 requirement(s) not met" in result.claims[0]
    assert "click-to-move" in ctx.brief_evidence
    assert result.commands[0]["cmd"] == "brief.review"

    passing = ReviewBriefSkill(cache, _brief_backend(met=True), path)
    result = passing.run(ctx)
    assert result.ok and result.claimed_props == frozenset({"brief.met"})
    assert ctx.brief_evidence == ""


@needs_git
def test_a_missing_or_unanswerable_brief_claims_nothing(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    absent = ReviewBriefSkill(ExpensiveResultCache(), _brief_backend(),
                              tmp_path / "nope.json")
    assert absent.run(ctx).claimed_props == frozenset()

    path = tmp_path / "brief.json"
    write_brief("t", "library", _brief_backend(), path)

    class Down(FakeBackend):
        def complete(self, prompt):
            if "decide from the source alone" in prompt:
                raise RuntimeError("model down")
            return "{}"

    result = ReviewBriefSkill(ExpensiveResultCache(), Down("x"), path).run(ctx)
    assert not result.ok and result.claimed_props == frozenset()
    assert "could not be run" in result.claims[0]


# ------------------------------------------------------------ end to end

@needs_git
def test_an_episode_freezes_its_brief_journals_it_and_is_held_to_it(
        repo, tmp_path):
    """The fixed iterative contract carries brief.met, the requirements are
    journaled with their hash, and what the review found missing reaches
    the next authoring prompt."""
    prompts = []

    def responder(prompt):
        prompts.append(prompt)
        if "Ask between" in prompt:
            return json.dumps({"questions": QUESTIONS})
        if "turn your answers into requirements" in prompt:
            return json.dumps({"answers": [], "requirements": REQUIREMENTS})
        if "decide from the source alone" in prompt:
            return json.dumps({"verdicts": [
                {"id": "pieces-are-modelled", "met": True, "evidence": "ok"},
                {"id": "click-to-move", "met": False,
                 "evidence": "no pointer handler anywhere in the source"}]})
        if "REPOSITORY FILES" in prompt:
            return ("FILE: calc.py\n```python\ndef add(a, b):\n"
                    "    return a + b\n```")
        return json.dumps({"goal": sorted(GOAL), "rationale": "green"})

    episode_dir = tmp_path / "episode"
    result, _, journal_path = run_repo_episode(
        repo, episode_dir, None, TEST_CMD, task_text="build a 3d chess game",
        backend=FakeBackend(responder), slots=["iteration1"],
        required_patch_slot="iteration1", product_kind="library",
        product_review=False, max_author_attempts=3)

    entries = Journal.read(str(journal_path))
    assert "brief.met" in entries[0]["goal"]
    assert "frozen before the work" in entries[0]["goal_rationale"]
    record = entries[0]["brief"]
    assert record["questions"] == QUESTIONS
    assert [r["id"] for r in record["requirements"]] == [
        "pieces-are-modelled", "click-to-move"]
    assert record["digest"] == load_brief(episode_dir / "brief.json").digest()

    # the brief lives outside the worktree the candidate writes to
    assert (episode_dir / "brief.json").is_file()
    assert not (episode_dir / "worktree" / "brief.json").exists()

    # judged, found wanting, and the finding reached the author
    assert not result.success
    reviews = [e for e in entries if e.get("t") == "EXEC"
               and e.get("action") == "ReviewBrief"]
    assert reviews and reviews[0]["ok"] is False
    assert any("WHAT THIS BUILD MUST BE" in p for p in prompts)
    assert any("no pointer handler anywhere" in p for p in prompts)


@needs_git
def test_a_resumed_episode_is_held_to_the_same_frozen_brief(repo, tmp_path):
    calls = {"briefs": 0}

    def responder(prompt):
        if "Ask between" in prompt:
            calls["briefs"] += 1
            return json.dumps({"questions": QUESTIONS})
        if "turn your answers into requirements" in prompt:
            return json.dumps({"answers": [], "requirements": REQUIREMENTS})
        if "decide from the source alone" in prompt:
            return json.dumps({"verdicts": []})
        if "REPOSITORY FILES" in prompt:
            return "FILE: calc.py\n```python\ndef add(a, b):\n    return a + b\n```"
        return json.dumps({"goal": sorted(GOAL), "rationale": "green"})

    episode_dir = tmp_path / "episode"
    common = dict(task_text="build a 3d chess game",
                  backend=FakeBackend(responder), slots=["iteration1"],
                  required_patch_slot="iteration1", product_kind="library",
                  product_review=False, max_author_attempts=2)
    run_repo_episode(repo, episode_dir, None, TEST_CMD, **common)
    first = load_brief(episode_dir / "brief.json").digest()
    run_repo_episode(repo, episode_dir, None, TEST_CMD, resume=True, **common)

    assert calls["briefs"] == 1, "the brief must be written once, not per run"
    assert load_brief(episode_dir / "brief.json").digest() == first
    segments = sorted(episode_dir.glob("episode*.jsonl"))
    assert len(segments) == 2
    for segment in segments:       # every chain states the bar it ran under
        assert Journal.read(str(segment))[0]["brief"]["digest"] == first
