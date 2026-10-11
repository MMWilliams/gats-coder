import numpy as np
import pytest

from hf import rhf

H2 = [("H", (0.0, 0.0, 0.0)), ("H", (0.0, 0.0, 1.4))]


def test_h2_total_energy():
    r = rhf(H2, 2)
    assert abs(r["energy"] - (-1.116714)) < 1e-5


def test_h2_orbital_energies():
    eps = rhf(H2, 2)["orbital_energies"]
    assert eps.shape == (2,)
    assert abs(eps[0] - (-0.5782)) < 1e-3 and abs(eps[1] - 0.6703) < 1e-3


def test_h2_one_electron_integrals():
    r = rhf(H2, 2)
    assert abs(r["S"][0, 1] - 0.6593) < 5e-4
    assert abs(r["T"][0, 0] - 0.7600) < 5e-4
    assert abs(r["T"][0, 1] - 0.2365) < 5e-4
    assert abs(r["nuclear_repulsion"] - 1 / 1.4) < 1e-12


def test_heh_cation():
    r = rhf([("He", (0.0, 0.0, 0.0)), ("H", (0.0, 0.0, 1.4632))], 2)
    assert abs(r["energy"] - (-2.860659)) < 1e-5


def test_helium_atom():
    r = rhf([("He", (0.0, 0.0, 0.0))], 2)
    assert abs(r["energy"] - (-2.643876)) < 1e-5
    assert r["iterations"] >= 1


def test_density_integrates_to_the_electron_count():
    r = rhf(H2, 2)
    assert abs(np.trace(r["P"] @ r["S"]) - 2.0) < 1e-8
