Implement `stiff.py` (numpy only; `numpy.linalg.solve` is fine, scipy is
blocked by the tests): an adaptive implicit ODE solver for stiff systems,
so that `python -m pytest tests -q` passes.

`solve_stiff(f, jac, t0, y0, t1, rtol=1e-6, atol=1e-9) -> (ts, ys)`

- `f(t, y) -> dy/dt` and `jac(t, y) -> d f / d y` (a dense matrix).
- Any A-stable (or L-stable) implicit method with error-based step control:
  BDF with variable order, Radau IIA, an SDIRK pair, or similar. Newton
  iteration on the implicit stage equations using `jac`.
- `ts[0] == t0`, `ts[-1] == t1` exactly, every accepted step recorded,
  `ys.shape == (len(ts), dim)`; a scalar `y0` is a 1-vector.

The tests integrate the Robertson kinetics problem to t = 1e5 and the Van
der Pol oscillator with mu = 1000, compare against tight scipy references,
and require each run to finish within its time limit using well under 5000
accepted steps. An explicit method cannot meet that: its stability limit
would need hundreds of millions of steps.
