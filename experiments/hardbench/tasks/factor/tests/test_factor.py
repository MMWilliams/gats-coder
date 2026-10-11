import random
import time

import pytest
from conftest import sympy

from factor import factor

x = sympy.Symbol("x")


def expected(coeffs):
    content, fs = sympy.Poly(coeffs, x).factor_list()
    out = [([int(c) for c in f.all_coeffs()], int(m)) for f, m in fs]
    return int(content), sorted(out, key=lambda fm: (len(fm[0]), fm[0]))


def check(coeffs, limit=20.0):
    t = time.time()
    got = factor(coeffs)
    assert time.time() - t < limit, f"took {time.time() - t:.1f}s"
    exp = expected(coeffs)
    assert got[0] == exp[0] and [(tuple(c), m) for c, m in got[1]] == \
        [(tuple(c), m) for c, m in exp[1]], (got, exp)


def poly(expr):
    return [int(c) for c in sympy.Poly(expr, x).all_coeffs()]


def test_basics():
    assert factor([2]) == (2, [])
    assert factor([-3]) == (-3, [])
    assert factor([0, 0, 2, -2]) == (2, [([1, -1], 1)])
    assert factor([-1, 0, 1]) == (-1, [([1, -1], 1), ([1, 1], 1)])
    with pytest.raises(ValueError):
        factor([0, 0])


@pytest.mark.parametrize("expr", [
    x**4 + 4, x**8 + 1, x**6 - 1, x**12 - 1, x**15 - 1,
    (x**2 + 1)**3 * (x - 2)**2, (2 * x + 3)**4 * (3 * x - 1),
    x**5 - x - 1, x**4 + x + 1, 6 * x**4 - 5 * x**3 - 29 * x**2 + 20 * x + 28,
    x**10 - 10 * x**8 + 38 * x**6 - 2 * x**5 - 100 * x**4 - 40 * x**3 + 121 * x**2 - 38 * x - 17,
    12 * x**6 + 4 * x**5 - 41 * x**4 - 3 * x**3 + 36 * x**2 - 12 * x - 8,
])
def test_known_polynomials(expr):
    check(poly(expr))


def test_cyclotomic_105_has_a_minus_two_coefficient():
    check(poly(sympy.cyclotomic_poly(105, x) * (x**2 - 1)))


@pytest.mark.parametrize("n", [3, 4, 5])
def test_swinnerton_dyer_is_irreducible(n):
    # degree 8, 16 and 32: they split into quadratics modulo every prime, so
    # the recombination step has to handle 4, 8 and 16 modular factors
    p = poly(sympy.polys.specialpolys.swinnerton_dyer_poly(n, x))
    t = time.time()
    content, fs = factor(p)
    assert time.time() - t < 120
    assert content == 1 and len(fs) == 1 and fs[0][1] == 1 and fs[0][0] == p


@pytest.mark.parametrize("seed", range(12))
def test_random_products(seed):
    rng = random.Random(seed)
    expr = sympy.Integer(rng.choice([1, -1, 2, 3]))
    for _ in range(rng.randint(1, 4)):
        d = rng.randint(1, 4)
        f = sum(rng.randint(-5, 5) * x**i for i in range(d)) + rng.randint(1, 3) * x**d
        expr *= f ** rng.randint(1, 2)
    check(poly(sympy.expand(expr)))
