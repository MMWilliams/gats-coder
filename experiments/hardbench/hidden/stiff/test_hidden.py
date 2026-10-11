import time

import numpy as np
from conftest import solve_ivp

from stiff import solve_stiff


def test_robertson_log_spaced_checkpoints():
    f = lambda t, y: np.array([-0.04 * y[0] + 1e4 * y[1] * y[2],
                               0.04 * y[0] - 1e4 * y[1] * y[2] - 3e7 * y[1] ** 2,
                               3e7 * y[1] ** 2])
    jac = lambda t, y: np.array([[-0.04, 1e4 * y[2], 1e4 * y[1]],
                                 [0.04, -1e4 * y[2] - 6e7 * y[1], -1e4 * y[1]],
                                 [0.0, 6e7 * y[1], 0.0]])
    y = np.array([1.0, 0.0, 0.0])
    t = 0.0
    for t1 in (1e-2, 1.0, 1e2, 1e4, 1e6):
        ref = solve_ivp(f, (t, t1), y, method="Radau", jac=jac, rtol=1e-10, atol=1e-14).y[:, -1]
        ts, ys = solve_stiff(f, jac, t, y, t1, rtol=1e-6, atol=1e-10)
        assert ts[0] == t and ts[-1] == t1 and len(ts) < 3000
        assert abs(ys[-1][0] - ref[0]) < 1e-4 * max(ref[0], 1e-3)
        assert abs(ys[-1][2] - ref[2]) < 1e-4 * max(ref[2], 1e-3)
        t, y = t1, ys[-1]


def test_hires_eight_species():
    def f(t, y):
        y1, y2, y3, y4, y5, y6, y7, y8 = y
        return np.array([
            -1.71 * y1 + 0.43 * y2 + 8.32 * y3 + 0.0007,
            1.71 * y1 - 8.75 * y2,
            -10.03 * y3 + 0.43 * y4 + 0.035 * y5,
            8.32 * y2 + 1.71 * y3 - 1.12 * y4,
            -1.745 * y5 + 0.43 * y6 + 0.43 * y7,
            -280 * y6 * y8 + 0.69 * y4 + 1.71 * y5 - 0.43 * y6 + 0.69 * y7,
            280 * y6 * y8 - 1.81 * y7,
            -280 * y6 * y8 + 1.81 * y7])

    def jac(t, y):
        J = np.zeros((8, 8))
        J[0, :3] = [-1.71, 0.43, 8.32]
        J[1, :2] = [1.71, -8.75]
        J[2, 2:5] = [-10.03, 0.43, 0.035]
        J[3, 1:4] = [8.32, 1.71, -1.12]
        J[4, 4:7] = [-1.745, 0.43, 0.43]
        J[5, 3:8] = [0.69, 1.71, -0.43 - 280 * y[7], 0.69, -280 * y[5]]
        J[6, 5:8] = [280 * y[7], -1.81, 280 * y[5]]
        J[7, 5:8] = [-280 * y[7], 1.81, -280 * y[5]]
        return J
    y0 = np.array([1, 0, 0, 0, 0, 0, 0, 0.0057])
    ref = solve_ivp(f, (0, 321.8122), y0, method="Radau", jac=jac, rtol=1e-10, atol=1e-14).y[:, -1]
    t = time.time()
    ts, ys = solve_stiff(f, jac, 0.0, y0, 321.8122, rtol=1e-7, atol=1e-10)
    assert time.time() - t < 60 and len(ts) < 3000
    assert np.max(np.abs(ys[-1] - ref)) < 1e-5


def test_order_of_accuracy_on_a_smooth_stiff_problem():
    lam = -1e4
    f = lambda t, y: np.array([lam * (y[0] - np.sin(t)) + np.cos(t)])
    jac = lambda t, y: np.array([[lam]])
    errs = []
    for rtol in (1e-3, 1e-5, 1e-7, 1e-9):
        ts, ys = solve_stiff(f, jac, 0.0, np.array([0.0]), 2.0, rtol=rtol, atol=rtol * 1e-3)
        errs.append(abs(ys[-1][0] - np.sin(2.0)))
    # Loose tolerances can share a step-size floor, so only the ends are compared.
    assert errs[3] < errs[1] / 100 and errs[3] < errs[0] / 100
    assert errs[3] < 1e-8


def test_a_discontinuity_in_the_forcing_does_not_stall():
    f = lambda t, y: np.array([-1e3 * (y[0] - (1.0 if t > 5 else 0.0))])
    jac = lambda t, y: np.array([[-1e3]])
    t = time.time()
    ts, ys = solve_stiff(f, jac, 0.0, np.array([0.0]), 10.0, rtol=1e-6, atol=1e-9)
    assert time.time() - t < 30 and len(ts) < 3000
    assert abs(ys[-1][0] - 1.0) < 1e-5
