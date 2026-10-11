"""Dormand-Prince 5(4) and Stormer-Verlet integrators. See TASK.md."""
import numpy as np


def dp5_step(f, t, y, h):
    raise NotImplementedError


def rk5_fixed(f, t0, y0, t1, h):
    raise NotImplementedError


def dopri45(f, t0, y0, t1, rtol=1e-6, atol=1e-9):
    raise NotImplementedError


def verlet(accel, x0, v0, t0, t1, h):
    raise NotImplementedError
