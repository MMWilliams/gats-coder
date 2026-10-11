import time

import numpy as np
from conftest import solve_ivp

from stiff import solve_stiff


def robertson(t, y):
    return np.array([-0.04 * y[0] + 1e4 * y[1] * y[2],
                     0.04 * y[0] - 1e4 * y[1] * y[2] - 3e7 * y[1] ** 2,
                     3e7 * y[1] ** 2])


def robertson_jac(t, y):
    return np.array([[-0.04, 1e4 * y[2], 1e4 * y[1]],
                     [0.04, -1e4 * y[2] - 6e7 * y[1], -1e4 * y[1]],
                     [0.0, 6e7 * y[1], 0.0]])


def vdp(mu):
    f = lambda t, y: np.array([y[1], mu * (1 - y[0] ** 2) * y[1] - y[0]])
    jac = lambda t, y: np.array([[0.0, 1.0], [-2 * mu * y[0] * y[1] - 1, mu * (1 - y[0] ** 2)]])
    return f, jac


def test_robertson_to_1e5():
    ref = solve_ivp(robertson, (0, 1e5), [1.0, 0.0, 0.0], method="Radau",
                    jac=robertson_jac, rtol=1e-10, atol=1e-14).y[:, -1]
    t = time.time()
    ts, ys = solve_stiff(robertson, robertson_jac, 0.0, np.array([1.0, 0.0, 0.0]), 1e5,
                         rtol=1e-6, atol=1e-10)
    assert time.time() - t < 60
    assert ts[0] == 0.0 and ts[-1] == 1e5 and ys.shape == (len(ts), 3)
    assert len(ts) < 5000, len(ts)
    assert abs(ys[-1][0] - ref[0]) < 1e-4 * ref[0]
    assert abs(ys[-1][2] - ref[2]) < 1e-4 * ref[2]
    assert abs(ys[-1][1] - ref[1]) < 1e-2 * ref[1]
    assert np.all(ys >= -1e-6) and np.allclose(ys.sum(axis=1), 1.0, atol=1e-6)


def test_van_der_pol_mu_1000():
    f, jac = vdp(1000.0)
    ref = solve_ivp(f, (0, 3000), [2.0, 0.0], method="Radau", jac=jac,
                    rtol=1e-10, atol=1e-12).y[:, -1]
    t = time.time()
    ts, ys = solve_stiff(f, jac, 0.0, np.array([2.0, 0.0]), 3000.0, rtol=1e-7, atol=1e-9)
    assert time.time() - t < 120
    assert len(ts) < 5000, len(ts)
    assert abs(ys[-1][0] - ref[0]) < 1e-3


def test_linear_stiff_system_exact():
    A = np.array([[-1.0, 0.0], [0.0, -1e6]])
    f = lambda t, y: A @ y
    jac = lambda t, y: A
    ts, ys = solve_stiff(f, jac, 0.0, np.array([1.0, 1.0]), 10.0, rtol=1e-8, atol=1e-12)
    assert len(ts) < 2000
    assert abs(ys[-1][0] - np.exp(-10.0)) < 1e-6
    assert abs(ys[-1][1]) < 1e-9


def test_scalar_and_tolerance_monotonicity():
    f = lambda t, y: -50 * (y - np.cos(t))
    jac = lambda t, y: np.array([[-50.0]])
    exact = lambda t: (50 * 50 * np.cos(t) + 50 * np.sin(t)) / (50 * 50 + 1) + (1 - 50 * 50 / (50 * 50 + 1)) * np.exp(-50 * t)
    errs, steps = [], []
    for rtol in (1e-4, 1e-6, 1e-8):
        ts, ys = solve_stiff(f, jac, 0.0, 1.0, 5.0, rtol=rtol, atol=rtol * 1e-2)
        assert ys.shape == (len(ts), 1)
        errs.append(abs(ys[-1][0] - exact(5.0)))
        steps.append(len(ts))
    assert errs[0] > errs[2] and steps[0] < steps[2]
    assert errs[2] < 1e-6
