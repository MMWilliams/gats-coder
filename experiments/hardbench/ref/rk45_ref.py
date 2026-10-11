"""Reference integrators used to calibrate the rk45 task's thresholds.

Never shipped to the task repo. If a threshold in the tests looks arbitrary,
it came from running this file.
"""
import math
import numpy as np

# Dormand-Prince 5(4) tableau
C = np.array([0, 1/5, 3/10, 4/5, 8/9, 1, 1])
A = [
    [],
    [1/5],
    [3/40, 9/40],
    [44/45, -56/15, 32/9],
    [19372/6561, -25360/2187, 64448/6561, -212/729],
    [9017/3168, -355/33, 46732/5247, 49/176, -5103/18656],
    [35/384, 0, 500/1113, 125/192, -2187/6784, 11/84],
]
B5 = np.array([35/384, 0, 500/1113, 125/192, -2187/6784, 11/84, 0])
B4 = np.array([5179/57600, 0, 7571/16695, 393/640, -92097/339200, 187/2100, 1/40])


def dp5_step(f, t, y, h):
    y = np.asarray(y, dtype=float)
    k = [np.asarray(f(t, y), dtype=float)]
    for i in range(1, 7):
        yi = y + h * sum(a * kk for a, kk in zip(A[i], k))
        k.append(np.asarray(f(t + C[i] * h, yi), dtype=float))
    y5 = y + h * sum(b * kk for b, kk in zip(B5, k))
    y4 = y + h * sum(b * kk for b, kk in zip(B4, k))
    return y5, y5 - y4, k[6]


def rk5_fixed(f, t0, y0, t1, h):
    t, y = t0, np.asarray(y0, dtype=float)
    n = int(round((t1 - t0) / h))
    for _ in range(n):
        y, _, _ = dp5_step(f, t, y, h)
        t += h
    return y


def dopri45(f, t0, y0, t1, rtol=1e-6, atol=1e-9):
    t, y = t0, np.asarray(y0, dtype=float)
    direction = 1.0 if t1 >= t0 else -1.0
    span = abs(t1 - t0)
    h = direction * min(span, 1e-2 * span if span else 1e-3)
    ts, ys = [t], [y.copy()]
    k1 = np.asarray(f(t, y), dtype=float)
    while direction * (t1 - t) > 1e-14 * max(1.0, abs(t1)):
        last = direction * (t + h - t1) >= 0
        if last:
            h = t1 - t
        y_new, err, k7 = dp5_step(f, t, y, h)
        sc = atol + rtol * np.maximum(np.abs(y), np.abs(y_new))
        e = math.sqrt(float(np.mean((err / sc) ** 2)))
        if e <= 1.0:
            t, y = (t1 if last else t + h), y_new
            ts.append(t); ys.append(y.copy())
            fac = 0.9 * (1.0 / e) ** 0.2 if e > 0 else 5.0
            h *= min(5.0, max(0.2, fac))
        else:
            h *= max(0.2, 0.9 * e ** -0.25)
    return np.array(ts), np.array(ys)


def verlet(accel, x0, v0, t0, t1, h):
    x, v = np.asarray(x0, dtype=float), np.asarray(v0, dtype=float)
    n = int(round((t1 - t0) / h))
    a = np.asarray(accel(x), dtype=float)
    xs, vs = [x.copy()], [v.copy()]
    for _ in range(n):
        v_half = v + 0.5 * h * a
        x = x + h * v_half
        a = np.asarray(accel(x), dtype=float)
        v = v_half + 0.5 * h * a
        xs.append(x.copy()); vs.append(v.copy())
    return np.array(xs), np.array(vs)


# ---- calibration problems ----
def kepler_state(e, t):
    """Exact Kepler orbit, a=1, mu=1: solve Kepler's equation for position/velocity."""
    M = t
    E = M if e < 0.8 else math.pi
    for _ in range(60):
        E -= (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
    x = math.cos(E) - e
    y = math.sqrt(1 - e * e) * math.sin(E)
    r = 1 - e * math.cos(E)
    vx = -math.sin(E) / r
    vy = math.sqrt(1 - e * e) * math.cos(E) / r
    return np.array([x, y]), np.array([vx, vy])


def kepler_f(t, s):
    r3 = (s[0] ** 2 + s[1] ** 2) ** 1.5
    return np.array([s[2], s[3], -s[0] / r3, -s[1] / r3])


def kepler_acc(x):
    return -x / (x[0] ** 2 + x[1] ** 2) ** 1.5


def energy(x, v):
    return 0.5 * (v[0] ** 2 + v[1] ** 2) - 1 / math.hypot(x[0], x[1])


if __name__ == "__main__":
    # order of rk5_fixed on y' = cos(t) y, y(0)=1  (y = exp(sin t))
    f = lambda t, y: np.cos(t) * y
    errs = [abs(rk5_fixed(f, 0.0, [1.0], 2.0, h)[0] - math.exp(math.sin(2.0))) for h in (0.2, 0.1, 0.05)]
    print("rk5 errs", errs, "slopes", [math.log2(errs[i] / errs[i + 1]) for i in range(2)])
    # order of verlet on harmonic oscillator
    errs = []
    for h in (0.1, 0.05, 0.025):
        xs, vs = verlet(lambda x: -x, [1.0], [0.0], 0.0, 10.0, h)
        errs.append(abs(xs[-1][0] - math.cos(10.0)))
    print("verlet errs", errs, "slopes", [math.log2(errs[i] / errs[i + 1]) for i in range(2)])
    # adaptive on Kepler e=0.5, 10 periods
    e = 0.5
    x0, v0 = kepler_state(e, 0.0)
    T = 2 * math.pi
    for rtol in (1e-6, 1e-9):
        ts, ys = dopri45(kepler_f, 0.0, np.r_[x0, v0], 10 * T, rtol=rtol, atol=rtol * 1e-3)
        xe, ve = kepler_state(e, 10 * T)
        print(f"dopri rtol={rtol}: steps={len(ts) - 1} pos err={np.linalg.norm(ys[-1][:2] - xe):.2e}")
    # verlet energy drift, Kepler e=0.5, 1000 periods, h=T/400
    h = T / 400
    xs, vs = verlet(kepler_acc, x0, v0, 0.0, 1000 * T, h)
    E0 = energy(x0, v0)
    Es = np.array([energy(x, v) for x, v in zip(xs[::400], vs[::400])])
    print(f"verlet energy: max rel dev {np.max(np.abs(Es - E0) / abs(E0)):.2e}, final {abs(Es[-1] - E0) / abs(E0):.2e}")
    # rk5 fixed on same, same step count: energy drift grows
    s = np.r_[x0, v0]
    t = 0.0
    worst = 0.0
    for i in range(400 * 1000):
        s, _, _ = dp5_step(kepler_f, t, s, h); t += h
        if i % 40000 == 0:
            worst = max(worst, abs(energy(s[:2], s[2:]) - E0) / abs(E0))
    print(f"rk5 fixed energy: final rel dev {abs(energy(s[:2], s[2:]) - E0) / abs(E0):.2e}")
