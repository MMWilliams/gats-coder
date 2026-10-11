import time

import pytest
from conftest import TC, exact_small, onsager_energy, onsager_magnetization

from ising import estimate_tc, simulate


def within(got, true, rel):
    assert abs(got - true) <= rel * abs(true), (got, true, rel)


@pytest.mark.parametrize("T", [2.0, 3.0])
def test_four_by_four_matches_exact_enumeration(T):
    e, m = exact_small(4, T)
    r = simulate(4, T, 20000, seed=2)
    within(r["energy"], e, 0.02)
    within(r["magnetization"], m, 0.02)


def test_onsager_energy_and_magnetisation_at_T_2():
    t = time.time()
    r = simulate(32, 2.0, 2000, seed=1)
    assert time.time() - t < 60
    within(r["energy"], onsager_energy(2.0), 0.01)
    within(r["magnetization"], onsager_magnetization(2.0), 0.015)


def test_binder_cumulant_is_universal_at_tc():
    t = time.time()
    r = simulate(16, TC, 4000, seed=1)
    assert time.time() - t < 60
    assert 0.58 < r["binder"] < 0.65, r["binder"]


def test_estimate_tc():
    t = time.time()
    tc = estimate_tc(sizes=(8, 16, 24), seed=0)
    assert time.time() - t < 180
    within(tc, TC, 0.015)


def test_seeded_runs_are_reproducible_and_seeds_matter():
    a = simulate(8, 2.3, 300, seed=1)
    assert a == simulate(8, 2.3, 300, seed=1)
    assert a != simulate(8, 2.3, 300, seed=2)
    assert set(a) == {"energy", "magnetization", "binder"}
