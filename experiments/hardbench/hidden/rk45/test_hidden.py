import math

import numpy as np
from scipy.integrate import solve_ivp

from integrators import dp5_step, dopri45, rk5_fixed, verlet

T = 2 * math.pi


def kepler_state(e, t):
    E = t
    for _ in range(60):
        E -= (E - e * math.sin(E) - t) / (1 - e * math.cos(E))
    r = 1 - e * math.cos(E)
    return (np.array([math.cos(E) - e, math.sqrt(1 - e * e) * math.sin(E)]),
            np.array([-math.sin(E) / r, math.sqrt(1 - e * e) * math.cos(E) / r]))


def kepler_f(t, s):
    r3 = (s[0] ** 2 + s[1] ** 2) ** 1.5
    return np.array([s[2], s[3], -s[0] / r3, -s[1] / r3])


def kepler_acc(x):
    return -x / (x[0] ** 2 + x[1] ** 2) ** 1.5


def energy(x, v):
    return 0.5 * (v[0] ** 2 + v[1] ** 2) - 1 / math.hypot(x[0], x[1])


def slopes(errs):
    return [math.log2(errs[i] / errs[i + 1]) for i in range(len(errs) - 1)]


def test_error_estimate_is_the_embedded_fourth_order_difference():
    f = lambda t, y: np.array([y[1], -y[0]])
    e = [np.linalg.norm(dp5_step(f, 0.0, np.array([1.0, 0.0]), h)[1]) for h in (0.4, 0.2)]
    assert 22 < e[0] / e[1] < 44        # err ~ h^5


def test_rk5_fixed_order_on_a_system():
    f = lambda t, y: np.array([y[1], -y[0]])
    errs = [np.linalg.norm(rk5_fixed(f, 0.0, np.array([1.0, 0.0]), 3.0, h) - np.array([math.cos(3.0), -math.sin(3.0)]))
            for h in (0.3, 0.15, 0.075)]
    assert all(4.5 < s < 5.6 for s in slopes(errs)), slopes(errs)


def test_arenstorf_orbit_closes():
    mu = 0.012277471
    mup = 1 - mu

    def f(t, y):
        x, yy, vx, vy = y
        d1 = ((x + mu) ** 2 + yy ** 2) ** 1.5
        d2 = ((x - mup) ** 2 + yy ** 2) ** 1.5
        return np.array([vx, vy,
                         x + 2 * vy - mup * (x + mu) / d1 - mu * (x - mup) / d2,
                         yy - 2 * vx - mup * yy / d1 - mu * yy / d2])
    y0 = np.array([0.994, 0.0, 0.0, -2.00158510637908252240537862224])
    period = 17.0652165601579625588917206249
    ts, ys = dopri45(f, 0.0, y0, period, rtol=1e-10, atol=1e-12)
    assert np.linalg.norm(ys[-1] - y0) < 1e-3
    assert len(ts) < 20000


def test_van_der_pol_against_scipy():
    f = lambda t, y: np.array([y[1], 5 * (1 - y[0] ** 2) * y[1] - y[0]])
    ref = solve_ivp(f, (0, 20), [2.0, 0.0], method="DOP853", rtol=1e-12, atol=1e-14)
    ts, ys = dopri45(f, 0.0, np.array([2.0, 0.0]), 20.0, rtol=1e-10, atol=1e-12)
    assert np.linalg.norm(ys[-1] - ref.y[:, -1]) < 1e-5


def test_scalar_initial_condition():
    ts, ys = dopri45(lambda t, y: -2 * y, 0.0, 1.0, 1.0, rtol=1e-9, atol=1e-12)
    assert ys.shape == (len(ts), 1)
    assert abs(ys[-1][0] - math.exp(-2.0)) < 1e-7


def test_tighter_tolerance_means_smaller_error_and_more_steps():
    x0, v0 = kepler_state(0.5, 0.0)
    xe, _ = kepler_state(0.5, 5 * T)
    out = {}
    for rtol in (1e-5, 1e-7, 1e-9):
        ts, ys = dopri45(kepler_f, 0.0, np.r_[x0, v0], 5 * T, rtol=rtol, atol=rtol * 1e-3)
        out[rtol] = (len(ts), np.linalg.norm(ys[-1][:2] - xe))
    assert out[1e-5][0] < out[1e-7][0] < out[1e-9][0]
    assert out[1e-5][1] > out[1e-7][1] > out[1e-9][1]


def test_verlet_bounded_energy_on_an_eccentric_orbit():
    x0, v0 = kepler_state(0.7, 0.0)
    E0 = energy(x0, v0)
    xs, vs = verlet(kepler_acc, x0, v0, 0.0, 500 * T, T / 400)
    dev = np.abs(np.array([energy(x, v) for x, v in zip(xs, vs)]) - E0) / abs(E0)
    assert dev.max() <= 1.5 * dev[:4000].max()
    assert xs.shape == (500 * 400 + 1, 2) and vs.shape == xs.shape


def test_rk5_fixed_is_not_secretly_symplectic():
    """A real Runge-Kutta method shows secular energy drift where Verlet does not."""
    x0, v0 = kepler_state(0.5, 0.0)
    E0 = energy(x0, v0)
    h = T / 100
    s = np.r_[x0, v0]
    first = 0.0
    for i in range(300 * 100):
        s, _ = dp5_step(kepler_f, i * h, s, h)
        if i < 1000:
            first = max(first, abs(energy(s[:2], s[2:]) - E0) / abs(E0))
    assert abs(energy(s[:2], s[2:]) - E0) / abs(E0) > 5 * first
