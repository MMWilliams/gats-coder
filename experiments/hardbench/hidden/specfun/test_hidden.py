import random

import pytest
from conftest import mp

import specfun

mp.mp.dps = 30
rng = random.Random(2026)


def close(got, true, tol=1e-12):
    true = float(true)
    assert abs(got - true) <= tol * max(1.0, abs(true)), (got, true)


def rel(got, true, tol=1e-12):
    true = float(true)
    assert abs(got - true) <= tol * abs(true), (got, true)


@pytest.mark.parametrize("x", [rng.uniform(-7, 7) for _ in range(25)])
def test_erf_random(x):
    close(specfun.erf(x), mp.erf(x))


@pytest.mark.parametrize("x", [rng.uniform(-3, 26) for _ in range(25)] + [26.5, 1e-300, -1e-300])
def test_erfc_random(x):
    rel(specfun.erfc(x), mp.erfc(x))


@pytest.mark.parametrize("x", [rng.uniform(0.01, 200) for _ in range(20)]
                         + [-0.1, -1.5, -3.99, -4.01, -15.5, -0.0001, 1e-6, 1e6, 170.5])
def test_lgamma_random(x):
    close(specfun.lgamma(x), mp.log(abs(mp.gamma(x))))


@pytest.mark.parametrize("x", [rng.uniform(0, 100) for _ in range(30)] + [-50.0, 1e-9])
def test_j0_j1_random(x):
    close(specfun.j0(x), mp.besselj(0, x))
    close(specfun.j1(x), mp.besselj(1, x))


@pytest.mark.parametrize("x", [rng.uniform(0.001, 100) for _ in range(30)] + [1e-6, 2.197141])
def test_y0_random(x):
    close(specfun.y0(x), mp.bessely(0, x))


@pytest.mark.parametrize("a,x", [(rng.uniform(0.1, 1000), None) for _ in range(30)])
def test_gammainc_near_the_transition(a, x):
    for x in (0.5 * a, a - 2 * a ** 0.5, a, a + 2 * a ** 0.5, 2 * a):
        x = max(x, 0.0)
        close(specfun.gammainc_p(a, x), mp.gammainc(a, 0, x, regularized=True))


def test_edge_values():
    assert specfun.erf(0.0) == 0.0 and specfun.erfc(0.0) == 1.0
    assert specfun.j0(0.0) == 1.0 and specfun.j1(0.0) == 0.0
    assert specfun.gammainc_p(3.0, 0.0) == 0.0
    close(specfun.lgamma(1.0), 0.0)
    close(specfun.lgamma(2.0), 0.0)
    close(specfun.lgamma(0.5), mp.log(mp.sqrt(mp.pi)))
