"""Context assembly (evidence, deterministic selection, project
instructions) and the CLI config/review surface."""

import json

import pytest
from conftest import BUGGY, FIXED, TEST_CMD, needs_git

import ledger.cli as cli
from ledger.executor import BlobStore, ExecContext, RunSuiteSkill
from ledger.llm import FakeBackend
from ledger.sensors import ExpensiveResultCache
from ledger.skills_llm import (AuthorPatchSkill, project_instructions,
                               select_context)


def _ctx(repo, tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    return ExecContext(worktree=repo, blobs=BlobStore(tmp_path / "blobs"),
                       test_cache=ExpensiveResultCache(), queue_dir=queue,
                       applied_dir=tmp_path / "applied")


# -- suite evidence ----------------------------------------------------------

@needs_git
def test_failing_suite_deposits_evidence(repo, tmp_path):
    ctx = _ctx(repo, tmp_path)
    RunSuiteSkill(TEST_CMD).run(ctx)
    assert "unit" in ctx.suite_evidence
    assert "test_add" in ctx.suite_evidence["unit"]["tail"]

    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    RunSuiteSkill(TEST_CMD).run(ctx)
    assert "unit" not in ctx.suite_evidence  # green run clears the evidence


# -- select_context ----------------------------------------------------------

@needs_git
def test_select_context_prefers_evidence_paths(repo):
    picked = select_context(repo, "FAILED test_calc.py::test_add - blah")
    assert picked and picked[0] == "test_calc.py"


@needs_git
def test_select_context_is_deterministic(repo):
    q = "something about the add function in calc.py"
    assert select_context(repo, q) == select_context(repo, q)
    assert select_context(repo, q)[0] == "calc.py"  # named path dominates


@needs_git
def test_select_context_empty_query_selects_nothing(repo):
    assert select_context(repo, "") == []


# -- project instructions ----------------------------------------------------

def test_project_instructions_precedence(tmp_path):
    assert project_instructions(tmp_path) == ""
    (tmp_path / "LEDGER.md").write_text("ledger rules", encoding="utf-8")
    assert project_instructions(tmp_path) == "ledger rules"
    (tmp_path / "AGENTS.md").write_text("agents rules", encoding="utf-8")
    assert project_instructions(tmp_path) == "agents rules"  # AGENTS.md wins


@needs_git
def test_author_prompt_carries_instructions_evidence_and_context(repo, tmp_path):
    (repo / "AGENTS.md").write_text("Never touch test files.",
                                    encoding="utf-8")
    ctx = _ctx(repo, tmp_path)
    RunSuiteSkill(TEST_CMD).run(ctx)  # deposit failing evidence
    prompts = []

    def capture(prompt: str) -> str:
        prompts.append(prompt)
        return "no diff"

    AuthorPatchSkill("a", FakeBackend(capture), "fix add()",
                     max_attempts=1).run(ctx)
    p = prompts[0]
    assert "PROJECT INSTRUCTIONS" in p and "Never touch test files." in p
    assert "MOST RECENT FAILING TEST OUTPUT" in p and "test_add" in p
    assert "RELEVANT FILE CONTENTS" in p and BUGGY in p


# -- CLI config resolution ---------------------------------------------------

def _write_cfg(root, cfg):
    d = root / ".ledger"
    d.mkdir(exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


def test_config_precedence_flags_over_file(tmp_path):
    _write_cfg(tmp_path, {"test_cmd": ["pytest", "-x"], "profile": "autonomous",
                          "planner": {**cli.PlanConfig().to_dict(),
                                      "budget": 4, "max_steps": 7}})
    cfg = cli._config(tmp_path)

    class A:
        test = None
        budget = None
        max_steps = None
        profile = None
        yes = False

    assert cli._test_cmd(A(), cfg) == ["pytest", "-x"]
    pc = cli._plan_config(A(), cfg)
    assert pc.budget == 4 and pc.max_steps == 7
    gate, profile = cli._gate_and_profile(A(), cfg)
    assert profile == "autonomous"

    class B(A):
        test = "pytest -q"
        budget = 12
        profile = "supervised"
        yes = True

    assert cli._test_cmd(B(), cfg) == ["pytest", "-q"]
    assert cli._plan_config(B(), cfg).budget == 12
    gate, profile = cli._gate_and_profile(B(), cfg)
    assert profile == "supervised"
    assert gate is cli.attended_gate  # --yes pre-approves


def test_missing_or_bad_config_is_empty(tmp_path):
    assert cli._config(tmp_path) == {}
    _write_cfg(tmp_path, {})
    (tmp_path / ".ledger" / "config.json").write_text("{not json",
                                                      encoding="utf-8")
    assert cli._config(tmp_path) == {}


def test_unknown_profile_exits(tmp_path):
    class A:
        profile = "cowboy"
        yes = False

    with pytest.raises(SystemExit):
        cli._gate_and_profile(A(), {})


# -- ledger review -----------------------------------------------------------

@needs_git
def test_review_reports_findings(repo, monkeypatch, capsys):
    (repo / "calc.py").write_text(BUGGY.replace("a - b", "a - b  # oops"),
                                  encoding="utf-8")
    monkeypatch.setattr(
        cli, "_backend",
        lambda args: FakeBackend("1. calc.py: subtraction bug remains."))
    rc = cli.main(["review", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "subtraction bug" in out


@needs_git
def test_review_empty_diff(repo, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_backend", lambda args: FakeBackend("unused"))
    rc = cli.main(["review", "--repo", str(repo)])
    assert rc == 0
    assert "nothing to review" in capsys.readouterr().out


@needs_git
def test_review_prompt_includes_diff_and_instructions(repo, monkeypatch):
    (repo / "AGENTS.md").write_text("Watch for sign errors.", encoding="utf-8")
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    seen = {}

    def capture(prompt: str) -> str:
        seen["prompt"] = prompt
        return "the diff looks correct"

    monkeypatch.setattr(cli, "_backend",
                        lambda args: FakeBackend(capture))
    rc = cli.main(["review", "--repo", str(repo)])
    assert rc == 0
    assert "Watch for sign errors." in seen["prompt"]
    assert "return a + b" in seen["prompt"]
