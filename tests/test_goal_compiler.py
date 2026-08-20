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


def test_a_goal_cannot_name_an_internal_slot():
    """Regression: the compiler put `patch.applied:a` in a goal. Which slot
    carried the fix is LEDGER's own bookkeeping -- the planner is free to
    use slot b, and then a green suite still counts as failure."""
    from ledger.goal import goal_vocabulary
    from ledger.skills_llm import build_authoring_catalog

    catalog = build_authoring_catalog(["a", "b"])
    offered = goal_vocabulary(catalog)
    assert "tests.pass:unit" in offered and "repo.clean" in offered
    for hidden in ("patch.applied:a", "patch.queued:b", "budget.llm:ok"):
        assert hidden not in offered, f"{hidden} must not be goal material"


def test_a_compiler_that_invents_a_slot_prop_is_rejected():
    from ledger.goal import GoalCompileError, compile_goal, goal_vocabulary
    from ledger.skills_llm import build_authoring_catalog

    catalog = build_authoring_catalog(["a"])
    backend = FakeBackend(json.dumps(
        {"goal": ["tests.pass:unit", "patch.applied:a"], "rationale": "x"}))
    with pytest.raises(GoalCompileError):
        compile_goal("fix it", goal_vocabulary(catalog), backend)


def test_a_goal_cannot_demand_failing_tests():
    """Regression: the compiler produced `tests.fail_count:unit:1-5 AND
    tests.pass:unit` -- red and green simultaneously. A failure state is
    never an outcome anyone wants."""
    from ledger.goal import goal_vocabulary
    from ledger.skills_llm import build_authoring_catalog

    offered = goal_vocabulary(build_authoring_catalog(["a"]))
    assert "tests.pass:unit" in offered
    assert not any(p.startswith("tests.fail_count:") for p in offered), (
        "a goal must never ask for failing tests")
