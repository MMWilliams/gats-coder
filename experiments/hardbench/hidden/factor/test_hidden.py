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


def check(coeffs, limit=30.0):
    t = time.time()
    got = factor(coeffs)
    assert time.time() - t < limit, f"took {time.time() - t:.1f}s"
    exp = expected(coeffs)
    assert got[0] == exp[0] and [(tuple(c), m) for c, m in got[1]] == \
        [(tuple(c), m) for c, m in exp[1]], (got, exp)


def poly(expr):
    return [int(c) for c in sympy.Poly(expr, x).all_coeffs()]


@pytest.mark.parametrize("seed", range(20, 45))
def test_random_products_unseen(seed):
    rng = random.Random(seed)
    expr = sympy.Integer(rng.choice([1, -1, 5, -6]))
    for _ in range(rng.randint(1, 5)):
        d = rng.randint(1, 6)
        f = sum(rng.randint(-20, 20) * x**i for i in range(d)) + rng.randint(1, 7) * x**d
        expr *= f ** rng.randint(1, 3)
    check(poly(sympy.expand(expr)))


@pytest.mark.parametrize("n", [20, 30, 60])
def test_x_n_minus_1(n):
    check(poly(x**n - 1))


def test_swinnerton_dyer_products():
    p4 = sympy.polys.specialpolys.swinnerton_dyer_poly(4, x)
    p5 = sympy.polys.specialpolys.swinnerton_dyer_poly(5, x)
    check(poly(sympy.expand(p4 * (x**4 + 4) * (x - 1))), limit=60)
    check(poly(sympy.expand(p5 * (x**2 + 1))), limit=120)


def test_large_coefficients_and_high_multiplicity():
    check(poly(sympy.expand((x**2 - 2 * x + 2)**5 * (123456789 * x + 987654321)**2 * 7)))
    check(poly(sympy.expand((x**3 + x + 1)**4 * (x**3 - x - 1)**4)))


def test_leading_zeros_and_monomials():
    assert factor([0, 0, 1, 0, 0]) == (1, [([1, 0], 2)])
    assert factor([0, 5, 0]) == (5, [([1, 0], 1)])
    check([0, 0, 0] + poly(x**4 + 4))


def test_irreducible_of_degree_12_with_many_modular_factors():
    # (x^2+1)(x^2+2)(x^2+3)... style resultant: irreducible over Z, splits mod small primes
    p = sympy.polys.specialpolys.swinnerton_dyer_poly(3, x)
    q = sympy.Poly(p, x).compose(sympy.Poly(x**2 + x, x))
    check([int(c) for c in q.all_coeffs()], limit=60)
