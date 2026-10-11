import math

import numpy as np
import pytest

from integrators import dp5_step, dopri45, rk5_fixed, verlet

T = 2 * math.pi


def kepler_state(e, t):
    E = t
    for _ in range(60):
        E -= (E - e * math.sin(E) - t) / (1 - e * math.cos(E))
    r = 1 - e * math.cos(E)
    x = np.array([math.cos(E) - e, math.sqrt(1 - e * e) * math.sin(E)])
    v = np.array([-math.sin(E) / r, math.sqrt(1 - e * e) * math.cos(E) / r])
    return x, v


def kepler_f(t, s):
    r3 = (s[0] ** 2 + s[1] ** 2) ** 1.5
    return np.array([s[2], s[3], -s[0] / r3, -s[1] / r3])


def kepler_acc(x):
    return -x / (x[0] ** 2 + x[1] ** 2) ** 1.5


def energy(x, v):
    return 0.5 * (v[0] ** 2 + v[1] ** 2) - 1 / math.hypot(x[0], x[1])


def slopes(errs):
    return [math.log2(errs[i] / errs[i + 1]) for i in range(len(errs) - 1)]


def test_dp5_step_is_fifth_order_locally():
    f = lambda t, y: y
    errs = [abs(dp5_step(f, 0.0, np.array([1.0]), h)[0][0] - math.exp(h)) for h in (0.2, 0.1)]
    assert 40 < errs[0] / errs[1] < 90          # local error ~ h^6


def test_rk5_fixed_is_fifth_order_globally():
    f = lambda t, y: np.cos(t) * y
    errs = [abs(rk5_fixed(f, 0.0, np.array([1.0]), 2.0, h)[0] - math.exp(math.sin(2.0)))
            for h in (0.2, 0.1, 0.05)]
    assert all(4.6 < s < 5.6 for s in slopes(errs)), slopes(errs)


def test_verlet_is_second_order():
    errs = []
    for h in (0.1, 0.05, 0.025):
        xs, vs = verlet(lambda x: -x, np.array([1.0]), np.array([0.0]), 0.0, 10.0, h)
        errs.append(abs(xs[-1][0] - math.cos(10.0)))
    assert all(1.8 < s < 2.2 for s in slopes(errs)), slopes(errs)


def test_dopri_kepler_ten_orbits():
    x0, v0 = kepler_state(0.5, 0.0)
    ts, ys = dopri45(kepler_f, 0.0, np.r_[x0, v0], 10 * T, rtol=1e-9, atol=1e-12)
    xe, _ = kepler_state(0.5, 10 * T)
    assert ts[0] == 0.0 and ts[-1] == 10 * T
    assert np.allclose(ys[0], np.r_[x0, v0])
    assert ys.shape == (len(ts), 4)
    assert np.linalg.norm(ys[-1][:2] - xe) < 1e-4


def test_dopri_adapts_to_tolerance_and_to_the_orbit():
    x0, v0 = kepler_state(0.5, 0.0)
    n6 = len(dopri45(kepler_f, 0.0, np.r_[x0, v0], 10 * T, rtol=1e-6, atol=1e-9)[0]) - 1
    n9 = len(dopri45(kepler_f, 0.0, np.r_[x0, v0], 10 * T, rtol=1e-9, atol=1e-12)[0]) - 1
    assert n6 < 800 and n9 > 1.5 * n6, (n6, n9)
    x0, v0 = kepler_state(0.7, 0.0)
    ts, _ = dopri45(kepler_f, 0.0, np.r_[x0, v0], 3 * T, rtol=1e-8, atol=1e-11)
    dt = np.diff(ts)
    assert dt.min() / dt.max() < 0.2       # small steps at perihelion, large at aphelion


def test_dopri_integrates_backward():
    ts, ys = dopri45(lambda t, y: -y, 1.0, np.array([math.exp(-1.0)]), 0.0, rtol=1e-9, atol=1e-12)
    assert ts[-1] == 0.0 and abs(ys[-1][0] - 1.0) < 1e-7


def test_verlet_energy_error_is_bounded_over_a_thousand_orbits():
    x0, v0 = kepler_state(0.5, 0.0)
    E0 = energy(x0, v0)
    xs, vs = verlet(kepler_acc, x0, v0, 0.0, 1000 * T, T / 100)
    dev = np.abs(np.array([energy(x, v) for x, v in zip(xs, vs)]) - E0) / abs(E0)
    first = dev[:10 * 100].max()
    assert first < 0.02
    assert dev.max() <= 1.5 * first, (first, dev.max())   # symplectic: no secular drift
