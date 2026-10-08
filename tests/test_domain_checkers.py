from fractions import Fraction

import pytest

from trusted_control.checkers import (polynomial_identity, score_held_out,
                                      verify_linear_constraints)
from trusted_control.cli import main as checker_main


def test_polynomial_identity_is_exact_and_rejects_code_execution():
    assert polynomial_identity("n*(n+1)/2", "(n**2+n)/2")
    assert polynomial_identity("(n+1)**2", "n**2+2*n+1")
    assert not polynomial_identity("n**2+1", "n**2")
    assert polynomial_identity("n**0", "1")
    with pytest.raises(ValueError):
        polynomial_identity("__import__('os').system('echo unsafe')", "0")


def test_science_score_requires_all_operator_held_cases():
    reference = {"case-a": 2.0, "case-b": 3.0, "case-c": 4.0}
    good = score_held_out(reference, dict(reference), 0.1)
    assert good.accepted and good.rmse == 0
    assert not score_held_out(reference, dict.fromkeys(reference, 0.0), 0.1).accepted
    with pytest.raises(ValueError, match="matching"):
        score_held_out(reference, {"case-a": 2.0}, 0.1)


def test_reasoning_certificate_is_independently_checked():
    constraints = [({"x": 2, "y": 1}, "==", 7),
                   ({"x": 1}, ">=", 2)]
    assert verify_linear_constraints({"x": 2, "y": 3}, constraints)
    assert not verify_linear_constraints({"x": 1, "y": 5}, constraints)
    with pytest.raises(ValueError, match="invalid"):
        verify_linear_constraints({"x": Fraction(1, 2)},
                                  [({"z": 1}, "==", 0)])


def test_operator_cli_checks_candidate_data_and_rejects_path_escape(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "formula.txt").write_text("n*(n+1)/2", encoding="utf-8")
    reference = tmp_path / "trusted.json"
    reference.write_text('{"a": 1, "b": 2, "c": 3}', encoding="utf-8")
    assert checker_main(["polynomial", "(n**2+n)/2", "formula.txt",
                         str(candidate)]) == 0
    assert checker_main(["polynomial", "n**2", "../trusted.json",
                         str(candidate)]) == 1

    (candidate / "predictions.json").write_text(
        '{"a": 1, "b": 2, "c": 3}', encoding="utf-8")
    assert checker_main(["science", str(reference), "predictions.json",
                         "0.1", str(candidate)]) == 0

    constraints = tmp_path / "constraints.json"
    constraints.write_text(
        '[{"coefficients": {"x": 1}, "relation": "==", "target": 2}]',
        encoding="utf-8")
    (candidate / "assignment.json").write_text('{"x": 2}', encoding="utf-8")
    assert checker_main(["reasoning", str(constraints), "assignment.json",
                         str(candidate)]) == 0
