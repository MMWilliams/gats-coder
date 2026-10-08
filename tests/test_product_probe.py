"""Inferring what a usable product needs, and checking it (SS15/16).

A chess game shipped with a test API and no way to click a piece, because
the loop had told every build it was "a library". This is the layer that
infers the kind, states what such a thing needs, checks the part a program
can check, and puts the findings in front of the model that writes the code.
"""

import json
import struct
import zlib
from pathlib import Path

import pytest
from conftest import GOAL, TEST_CMD, needs_git

from ledger.executor import (ExecContext, BlobStore, ProbeProductSkill,
                             resume_product_concerns, reviewed_already,
                             run_repo_episode)
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.product import (PROBED_KINDS, Concern, concerns_block,
                            input_wiring)
from ledger.repo import content_fingerprint
from ledger.sensors import ExpensiveResultCache, product_sensor
from ledger.skills_llm import build_authoring_catalog, probe_product_action
from ledger.vision import ensure_built, looks_blank, page_root, png_stats


# ------------------------------------------------------- input wiring

def test_input_wiring_finds_handlers_and_skips_build_output(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.js").write_text(
        "const c = document.querySelector('canvas');\n"
        "c.addEventListener('pointerdown', onDown);\n"
        "window.onkeydown = (e) => move(e.key);\n", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<button onclick="newGame()">New</button>', encoding="utf-8")
    for junk in ("node_modules/three/build/three.js", "dist/assets/app.js"):
        path = tmp_path / junk
        path.parent.mkdir(parents=True)
        path.write_text("x.addEventListener('click', y)", encoding="utf-8")

    hits = input_wiring(tmp_path)
    assert hits == ["index.html:1", "src/main.js:2", "src/main.js:3"]


def test_input_wiring_is_empty_for_a_build_nobody_can_operate(tmp_path):
    (tmp_path / "api.js").write_text(
        "window.CHESS_TEST = {playerMove(a, b) { return game.move(a, b); }};\n"
        "// listens for nothing\n", encoding="utf-8")
    assert input_wiring(tmp_path) == []


# ------------------------------------------------------ concerns block

def test_review_findings_become_authoring_evidence():
    concerns = [Concern("controls", "no way to move a piece by hand",
                        "click two squares and watch the FEN change")]
    block = concerns_block("game", concerns)
    assert "A GAME" in block
    assert "controls: no way to move a piece by hand" in block
    assert "click two squares" in block
    # the journal's dict form rebuilds the same block on resume
    assert concerns_block("game", [{"dimension": "controls",
                                    "concern": "no way to move a piece by hand",
                                    "check": "click two squares and watch the FEN change"}]) == block
    assert concerns_block("game", []) == ""


def test_resume_reads_the_review_from_the_journal(tmp_path):
    with Journal(str(tmp_path / "episode.jsonl")) as j:
        j.append("HEADER", task_id="t")
        j.append("PRODUCT_REVIEW", kind="game",
                 concerns=[{"dimension": "challenge", "concern": "no opponent",
                            "check": "the AI replies"}])
    assert reviewed_already(tmp_path)
    assert "challenge: no opponent" in resume_product_concerns(tmp_path)
    assert not reviewed_already(tmp_path / "elsewhere")
    assert resume_product_concerns(tmp_path / "elsewhere") == ""


# ------------------------------------------------------------ png stats

def _png(path: Path, width: int, height: int, pixel, filter_type: int = 0):
    rows = bytearray()
    prior = bytes(width * 3)
    for y in range(height):
        raw = bytes(b for x in range(width) for b in pixel(x, y))
        if filter_type == 1:                       # Sub, as an encoder would
            enc = bytearray(raw)
            for i in range(len(raw) - 1, 2, -1):
                enc[i] = (raw[i] - raw[i - 3]) & 0xFF
            rows += b"\x01" + bytes(enc)
        elif filter_type == 2:                     # Up
            rows += b"\x02" + bytes((raw[i] - prior[i]) & 0xFF
                                    for i in range(len(raw)))
        else:
            rows += b"\x00" + raw
        prior = raw

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(bytes(rows)))
                     + chunk(b"IEND", b""))
    return path


@pytest.mark.parametrize("filter_type", [0, 1, 2])
def test_a_flat_screen_is_blank_and_a_scene_is_not(tmp_path, filter_type):
    flat = _png(tmp_path / "flat.png", 64, 48, lambda x, y: (20, 20, 40),
                filter_type)
    scene = _png(tmp_path / "scene.png", 64, 48,
                 lambda x, y: ((x * 7) % 256, (y * 5) % 256, (x + y) % 256),
                 filter_type)
    assert looks_blank(png_stats(flat))
    stats = png_stats(scene)
    assert stats["width"] == 64 and stats["distinct"] > 40
    assert not looks_blank(stats)


def test_an_unreadable_image_is_not_assessed_rather_than_blank(tmp_path):
    (tmp_path / "nope.png").write_bytes(b"not a png")
    assert png_stats(tmp_path / "nope.png") is None
    assert looks_blank(None) is False


def test_the_built_output_is_what_gets_photographed(tmp_path):
    assert page_root(tmp_path) is None
    (tmp_path / "index.html").write_text("<script type=module src=/src/main.js>",
                                         encoding="utf-8")
    assert page_root(tmp_path) == tmp_path
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("built", encoding="utf-8")
    assert page_root(tmp_path) == tmp_path / "dist"
    assert ensure_built(tmp_path) is True        # nothing to build


# -------------------------------------------------------- the contract

def test_probe_is_a_verifier_that_needs_a_passing_suite_first():
    a = probe_product_action()
    assert a.prec == frozenset({"tests.pass:unit"})
    assert a.add == frozenset({"product.usable"})
    assert a.deterministic is False and a.effects_known is True


def test_applying_a_patch_invalidates_the_usability_and_design_verdicts():
    catalog = build_authoring_catalog(["a"])
    apply = next(x for x in catalog if x.name == "ApplyPatch:a")
    assert {"product.usable", "design.ok"} <= apply.delete


def test_the_sensor_reads_the_probe_cache_and_never_guesses():
    cache = ExpensiveResultCache()
    sensor = product_sensor(cache, lambda root: "fp")
    assert sensor.probe(Path(".")) == set()
    cache.put("fp", {"product.usable"})
    assert sensor.probe(Path(".")) == {"product.usable"}


# ------------------------------------------------------------ the skill

def _ctx(repo, tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    return ExecContext(worktree=repo, blobs=BlobStore(tmp_path / "blobs"),
                       test_cache=ExpensiveResultCache(), queue_dir=queue,
                       applied_dir=tmp_path / "applied")


@needs_git
def test_probe_names_what_is_missing_then_passes_once_it_is_there(
        repo, tmp_path, monkeypatch):
    monkeypatch.setattr("ledger.vision.screenshot", lambda *a, **k: False)
    ctx = _ctx(repo, tmp_path)
    cache = ExpensiveResultCache()
    skill = ProbeProductSkill(cache, "game")

    result = skill.run(ctx)
    assert not result.ok and result.claimed_props == frozenset()
    assert cache.get(content_fingerprint(repo)) == frozenset()
    assert "no handler for any pointer, key or touch event" in ctx.product_evidence
    assert "not assessed" in ctx.product_evidence     # no page: not a failure
    assert result.commands[0]["cmd"] == "product.probe"

    (repo / "main.js").write_text(
        "canvas.addEventListener('pointerdown', pick);\n", encoding="utf-8")
    result = skill.run(ctx)
    assert result.ok and result.claimed_props == frozenset({"product.usable"})
    assert ctx.product_evidence == ""


@needs_git
def test_a_game_episode_requires_usability_and_feeds_the_probe_back(
        repo, tmp_path, monkeypatch):
    """The fixed iterative contract for a game is not satisfied by a green
    suite alone, and what the probe finds missing reaches the next
    authoring prompt as evidence."""
    monkeypatch.setattr("ledger.vision.screenshot", lambda *a, **k: False)
    prompts = []

    def responder(prompt):
        prompts.append(prompt)
        if "REPOSITORY FILES" in prompt:
            return ("FILE: calc.py\n```python\ndef add(a, b):\n"
                    "    return a + b\n```")
        return json.dumps({"goal": sorted(GOAL), "rationale": "green"})

    result, _, journal = run_repo_episode(
        repo, tmp_path / "episode", None, TEST_CMD, task_text="build the game",
        backend=FakeBackend(responder), slots=["iteration1"],
        required_patch_slot="iteration1", product_kind="game",
        product_review=False, max_author_attempts=2)
    entries = Journal.read(str(journal))
    assert "product.usable" in entries[0]["goal"]
    assert not result.success                    # calc.py listens for nobody
    probes = [e for e in entries if e.get("t") == "EXEC"
              and e.get("action") == "ProbeProduct"]
    assert probes and probes[0]["ok"] is False
    assert any("USABILITY PROBE OF THE BUILD" in p for p in prompts)


def test_the_author_prompt_carries_review_gaps_and_probe_findings(tmp_path):
    from ledger.skills_llm import AuthorPatchSkill
    (tmp_path / "main.js").write_text("// hi", encoding="utf-8")
    ctx = _ctx(tmp_path, tmp_path / "ep")
    ctx.product_concerns = "WHAT THE CONTRACT DOES NOT GUARANTEE FOR A GAME\n- controls: none"
    ctx.product_evidence = "USABILITY PROBE OF THE BUILD\n  1. no handler"
    prompt = AuthorPatchSkill("a", FakeBackend("x"), "build a game",
                              product_kind="game")._prompt(ctx)
    assert "WHAT MAKES A GAME ACTUALLY USABLE" in prompt
    assert "controls: none" in prompt
    assert "1. no handler" in prompt


# ---------------------------------------------- loop/goal mode forwarding

def _capture_run(monkeypatch):
    from ledger import cli
    seen = []
    monkeypatch.setattr(cli, "cmd_run", lambda args: (seen.append(args), 0)[1])
    return seen


def test_iterative_mode_infers_the_kind_instead_of_calling_everything_a_library(
        tmp_path, monkeypatch):
    from ledger import cli
    seen = _capture_run(monkeypatch)
    assert cli.main(["loop", "build a three.js chess game with an AI opponent",
                     "--repo", str(tmp_path), "--name", "t",
                     "--max-iterations", "1"]) == 0
    args = seen[0]
    assert args.product_kind == "auto"
    assert args.product_review is True        # first run: review it
    assert args.design_review is True         # a game gets photographed
    assert args.launch is False               # never pops a browser per turn
    assert "game" in PROBED_KINDS


def test_iterative_mode_reviews_once_and_skips_pictures_for_a_library(
        tmp_path, monkeypatch):
    from ledger import cli
    seen = _capture_run(monkeypatch)
    episode = tmp_path / ".ledger" / "episodes" / "t"
    episode.mkdir(parents=True)
    with Journal(str(episode / "episode.jsonl")) as j:
        j.append("HEADER", task_id="t")
        j.append("PRODUCT_REVIEW", kind="library", concerns=[])
    assert cli.main(["loop", "add a parser to the python library",
                     "--repo", str(tmp_path), "--name", "t",
                     "--max-iterations", "1"]) == 0
    args = seen[0]
    assert args.product_review is False       # already journaled
    assert args.design_review is False        # nothing to photograph
    assert args.resume is True
