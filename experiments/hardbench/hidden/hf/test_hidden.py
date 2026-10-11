import math

import numpy as np

from hf import rhf


def h2(R):
    return [("H", (0.0, 0.0, 0.0)), ("H", (0.0, 0.0, R))]


def test_h2_potential_curve():
    for R, E in ((1.2, -1.110334), (1.35, -1.117501), (2.0, -1.049171), (3.0, -0.885275)):
        assert abs(rhf(h2(R), 2)["energy"] - E) < 1e-5, R


def test_h2_minimum_is_near_1_35():
    e = {R: rhf(h2(R), 2)["energy"] for R in (1.3, 1.35, 1.4)}
    assert e[1.35] < e[1.3] and e[1.35] < e[1.4]


def test_h3_cation_equilateral():
    s = 1.65
    tri = [("H", (0, 0, 0)), ("H", (s, 0, 0)), ("H", (s / 2, s * math.sqrt(3) / 2, 0))]
    assert abs(rhf(tri, 2)["energy"] - (-1.237548)) < 1e-5


def test_h4_square_two_occupied_orbitals():
    sq = [("H", (0, 0, 0)), ("H", (1.6, 0, 0)), ("H", (0, 1.6, 0)), ("H", (1.6, 1.6, 0))]
    r = rhf(sq, 4)
    assert abs(r["energy"] - (-1.658605)) < 1e-5
    assert abs(np.trace(r["P"] @ r["S"]) - 4.0) < 1e-8


def test_two_electron_integral_symmetry_and_value():
    r = rhf(h2(1.4), 2)
    eri = r["eri"]
    assert eri.shape == (2, 2, 2, 2)
    assert np.allclose(eri, eri.transpose(1, 0, 2, 3))
    assert np.allclose(eri, eri.transpose(2, 3, 0, 1))
    assert abs(eri[0, 0, 0, 0] - 0.7746) < 5e-4      # (11|11), Szabo & Ostlund


def test_invariance_under_translation_and_rotation():
    e0 = rhf(h2(1.4), 2)["energy"]
    shifted = [("H", (1.0, 2.0, 3.0)), ("H", (1.0, 2.0, 4.4))]
    rotated = [("H", (0.0, 0.0, 0.0)), ("H", (1.4 / math.sqrt(3),) * 3)]
    assert abs(rhf(shifted, 2)["energy"] - e0) < 1e-9
    assert abs(rhf(rotated, 2)["energy"] - e0) < 1e-9


def test_orbital_energies_ascending_and_convergence_fast():
    r = rhf(h2(1.4), 2)
    eps = r["orbital_energies"]
    assert np.all(np.diff(eps) >= 0)
    assert r["iterations"] < 100
    assert abs(r["electronic"] + r["nuclear_repulsion"] - r["energy"]) < 1e-12
