"""Small executable checkers for bounded math, science, and reasoning tasks.

The operator fixes inputs and thresholds before seeing a candidate. These are
real checks for their stated contracts, not general proof or science engines.
Run them with independent inputs outside the candidate's write access.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping, Sequence


def _polynomial(expression: str, variable: str) -> dict[int, Fraction]:
    """Parse a restricted rational polynomial without evaluating Python code."""
    if len(expression) > 2000 or not variable.isidentifier():
        raise ValueError("invalid polynomial contract")

    def add(left, right):
        out = dict(left)
        for power, coefficient in right.items():
            out[power] = out.get(power, Fraction(0)) + coefficient
        return {p: c for p, c in out.items() if c}

    def multiply(left, right):
        out = {}
        for a, x in left.items():
            for b, y in right.items():
                if a + b > 64:
                    raise ValueError("polynomial degree exceeds limit")
                out[a + b] = out.get(a + b, Fraction(0)) + x * y
        return {p: c for p, c in out.items() if c}

    def parse(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            if abs(node.value) > 10**12:
                raise ValueError("integer coefficient exceeds limit")
            return {0: Fraction(node.value)} if node.value else {}
        if isinstance(node, ast.Name) and node.id == variable:
            return {1: Fraction(1)}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = parse(node.operand)
            return ({p: -c for p, c in value.items()}
                    if isinstance(node.op, ast.USub) else value)
        if isinstance(node, ast.BinOp):
            left = parse(node.left)
            if isinstance(node.op, ast.Pow):
                if isinstance(node.right, ast.Constant) and type(node.right.value) is int \
                        and 0 <= node.right.value <= 32:
                    out = {0: Fraction(1)}
                    for _ in range(node.right.value):
                        out = multiply(out, left)
                    return out
                raise ValueError("polynomial exponent must be a bounded integer")
            right = parse(node.right)
            if isinstance(node.op, ast.Add):
                return add(left, right)
            if isinstance(node.op, ast.Sub):
                return add(left, {p: -c for p, c in right.items()})
            if isinstance(node.op, ast.Mult):
                return multiply(left, right)
            if isinstance(node.op, ast.Div) and set(right) == {0} and right[0]:
                return {p: c / right[0] for p, c in left.items()}
        raise ValueError("only rational polynomial operations are accepted")

    return parse(ast.parse(expression, mode="eval").body)


def polynomial_identity(left: str, right: str, variable: str = "n") -> bool:
    """Exact identity in Q[variable]; does not certify arbitrary mathematics."""
    return _polynomial(left, variable) == _polynomial(right, variable)


@dataclass(frozen=True)
class ScienceScore:
    accepted: bool
    rmse: float
    cases: int
    reference_digest: str
    prediction_digest: str


def score_held_out(reference: Mapping[str, float],
                   predictions: Mapping[str, float],
                   maximum_rmse: float) -> ScienceScore:
    """Score fixed, operator-held observations against keyed predictions."""
    if len(reference) < 3 or reference.keys() != predictions.keys():
        raise ValueError("at least three matching held-out case IDs required")
    if maximum_rmse < 0 or not math.isfinite(maximum_rmse):
        raise ValueError("invalid preregistered threshold")
    ordered = sorted(reference)
    values = [(float(reference[k]), float(predictions[k])) for k in ordered]
    if not all(math.isfinite(a) and math.isfinite(b) for a, b in values):
        raise ValueError("non-finite observation or prediction")
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in values) / len(values))

    def digest(items):
        return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()

    return ScienceScore(rmse <= maximum_rmse, rmse, len(values),
                        digest(dict(reference)), digest(dict(predictions)))


def verify_linear_constraints(assignment: Mapping[str, int | Fraction],
                              constraints: Sequence[tuple[Mapping[str, int], str,
                                                          int]]) -> bool:
    """Check an exact assignment; no generated reasoning trace is trusted."""
    for coefficients, relation, target in constraints:
        if relation not in {"==", "<=", ">="} or not coefficients.keys() <= assignment.keys():
            raise ValueError("invalid constraint contract")
        left = sum((Fraction(coefficient) * Fraction(assignment[name])
                    for name, coefficient in coefficients.items()), Fraction(0))
        right = Fraction(target)
        if not ({"==": left == right, "<=": left <= right,
                 ">=": left >= right}[relation]):
            return False
    return True
