Implement `integrators.py` (numpy only) so that `python -m pytest tests -q` passes.

Four functions, all taking and returning numpy float arrays (a scalar y0 is
accepted and treated as a 1-vector; `ys`/`xs`/`vs` are always 2-D,
shape (len(ts), dim)):

- `dp5_step(f, t, y, h) -> (y_new, err)`: one Dormand-Prince 5(4) step.
  `y_new` is the 5th-order solution, `err` is the difference between the 5th-
  and 4th-order solutions (the embedded error estimate).
- `rk5_fixed(f, t0, y0, t1, h) -> y(t1)`: `round((t1 - t0) / h)` steps of
  `dp5_step`.
- `dopri45(f, t0, y0, t1, rtol=1e-6, atol=1e-9) -> (ts, ys)`: adaptive step
  size control on the embedded estimate (RMS of err / (atol + rtol * max(|y|,
  |y_new|)) must be <= 1 to accept; grow and shrink the step with the usual
  safety factor and limits). `ts[0] == t0` and `ts[-1] == t1` exactly, every
  accepted step is recorded, and `t1 < t0` integrates backward.
- `verlet(accel, x0, v0, t0, t1, h) -> (xs, vs)`: velocity Stormer-Verlet
  (kick-drift-kick), `accel(x) -> a`, `round((t1 - t0) / h)` steps, initial
  state included.

The tests measure orders of convergence and long-time energy behaviour, not
just values, so the methods must actually be the methods named.
