import time

import pytest
from conftest import TC, exact_small, onsager_energy

from ising import estimate_tc, simulate


def within(got, true, rel):
    assert abs(got - true) <= rel * abs(true), (got, true, rel)


@pytest.mark.parametrize("T", [1.5, TC])
def test_four_by_four_unseen_temperatures(T):
    e, m = exact_small(4, T)
    r = simulate(4, T, 20000, seed=7)
    within(r["energy"], e, 0.02)
    within(r["magnetization"], m, 0.02)


def test_finite_size_magnetisation_is_not_the_bulk_formula():
    # L = 8 at T = 2.2: the bulk value is 0.785; the lattice gives 0.824 (ref MC)
    r = simulate(8, 2.2, 4000, seed=5)
    within(r["magnetization"], 0.824, 0.03)


def test_paramagnetic_side_energy():
    r = simulate(32, 2.4, 4000, seed=4)
    within(r["energy"], onsager_energy(2.4), 0.015)


@pytest.mark.parametrize("L", [8, 24])
def test_binder_at_tc_other_sizes(L):
    r = simulate(L, TC, 4000, seed=3)
    assert 0.58 < r["binder"] < 0.65, r["binder"]


def test_estimate_tc_other_sizes_and_seed():
    t = time.time()
    tc = estimate_tc(sizes=(12, 24), seed=5)
    assert time.time() - t < 240
    within(tc, TC, 0.015)


def test_large_lattice_within_time():
    t = time.time()
    r = simulate(64, 2.0, 500, seed=3)
    assert time.time() - t < 60
    within(r["energy"], onsager_energy(2.0), 0.01)
