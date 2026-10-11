import pytest
from conftest import mp

import specfun

mp.mp.dps = 30


def close(got, true, tol=1e-12):
    true = float(true)
    assert abs(got - true) <= tol * max(1.0, abs(true)), (got, true)


def rel(got, true, tol=1e-12):
    true = float(true)
    assert abs(got - true) <= tol * abs(true), (got, true)


@pytest.mark.parametrize("x", [-6.0, -2.5, -1.0, -0.3, 0.0, 1e-8, 0.25, 0.9, 1.5, 2.0, 3.2, 6.0])
def test_erf(x):
    close(specfun.erf(x), mp.erf(x))


@pytest.mark.parametrize("x", [-5.0, -1.0, 0.0, 0.5, 1.0, 2.0, 3.5, 6.0, 10.0, 15.0, 26.0])
def test_erfc_keeps_relative_accuracy_in_the_tail(x):
    rel(specfun.erfc(x), mp.erfc(x))


@pytest.mark.parametrize("x", [0.5, 1.0, 1.5, 2.0, 3.7, 10.0, 25.5, 100.0, 1000.5,
                               0.001, -0.5, -2.5, -7.3, -20.25])
def test_lgamma_including_negative_arguments(x):
    close(specfun.lgamma(x), mp.log(abs(mp.gamma(x))))


@pytest.mark.parametrize("x", [0.0, 0.1, 1.0, 2.4048, 5.0, 8.65, 12.3, 30.0, 55.5, 100.0, -3.0])
def test_j0(x):
    close(specfun.j0(x), mp.besselj(0, x))


@pytest.mark.parametrize("x", [0.0, 0.2, 1.0, 3.8317, 7.0, 15.5, 40.0, 100.0, -2.0])
def test_j1(x):
    close(specfun.j1(x), mp.besselj(1, x))


@pytest.mark.parametrize("x", [0.001, 0.1, 0.8936, 1.0, 3.0, 7.5, 22.2, 50.0, 100.0])
def test_y0(x):
    close(specfun.y0(x), mp.bessely(0, x))


@pytest.mark.parametrize("a,x", [(0.5, 0.001), (0.5, 1.0), (1.0, 2.0), (2.5, 0.3), (3.0, 3.0),
                                 (10.0, 4.0), (10.0, 20.0), (50.0, 49.0), (100.0, 100.0),
                                 (300.0, 250.0), (1000.0, 1000.0), (1000.0, 1100.0), (7.0, 0.0)])
def test_regularised_lower_incomplete_gamma(a, x):
    close(specfun.gammainc_p(a, x), mp.gammainc(a, 0, x, regularized=True))
