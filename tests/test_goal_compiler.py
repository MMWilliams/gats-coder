"""Goal compiler: LLM proposes, the sensor vocabulary disposes (SS7.1)."""

import json

import pytest

from ledger.executor import build_coding_catalog
from ledger.goal import GoalCompileError, catalog_vocabulary, compile_goal
from ledger.llm import FakeBackend

VOCAB = catalog_vocabulary(build_coding_catalog(["fix-001"]))


def test_vocabulary_covers_coding_props():
    assert "tests.pass:unit" in VOCAB
    assert "repo.clean" in VOCAB
    assert "patch.applied:fix-001" in VOCAB


def test_valid_goal_compiles():
    backend = FakeBackend(json.dumps({
        "goal": ["tests.pass:unit", "repo.clean"],
        "rationale": "fix the failing test and leave the tree clean",
    }))
    contract = compile_goal("Fix the failing unit test and commit.",
                            VOCAB, backend)
    assert contract.goal == frozenset({"tests.pass:unit", "repo.clean"})
    assert contract.backend_id == "fake"
    assert "clean" in contract.rationale


def test_hallucinated_proposition_rejected():
    backend = FakeBackend(json.dumps({
        "goal": ["tests.pass:unit", "world.peace"], "rationale": "..."}))
    with pytest.raises(GoalCompileError, match="world.peace"):
        compile_goal("Fix it.", VOCAB, backend)


def test_unparseable_and_empty_rejected():
    with pytest.raises(GoalCompileError, match="parseable"):
        compile_goal("Fix it.", VOCAB, FakeBackend("sure, will do!"))
    with pytest.raises(GoalCompileError, match="empty"):
        compile_goal("Fix it.", VOCAB,
                     FakeBackend(json.dumps({"goal": []})))
