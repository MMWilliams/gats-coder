Implement `ising.py`: Monte Carlo for the 2-D Ising model on an L x L
periodic square lattice (J = 1, k_B = 1, no field), so that
`python -m pytest tests -q` passes within its time limits. Pure Python
with the standard library, or numpy; no scipy in the implementation.

`simulate(L, T, n_updates, seed) -> dict`

- Returns `{"energy": e, "magnetization": m, "binder": u}` averaged over
  `n_updates` Monte Carlo updates taken after the implementation's own
  thermalisation (at least `n_updates // 5` extra updates): `energy` is the
  mean energy per spin `-sum_<ij> s_i s_j / N`, `magnetization` is the mean
  of `|sum_i s_i| / N`, and `binder` is `1 - <m^4> / (3 <m^2>^2)`.
- Deterministic for a given `seed` (seed a `random.Random` or numpy
  generator from it).

`estimate_tc(sizes=(8, 16, 24), seed=0) -> float`

- The critical temperature from the crossing of the Binder cumulants of the
  two largest sizes, scanning a temperature grid around 2.27.

The tests compare against exact results: Onsager's energy and spontaneous
magnetisation for large lattices, and the exact partition function of a
4 x 4 lattice by enumeration (which differs from Onsager's values, so the
thermodynamics must come from the simulation, not a formula), plus the
universal Binder cumulant at T_c = 2 / ln(1 + sqrt 2) = 2.2692. Near T_c
the limits are set for a cluster algorithm (Wolff or Swendsen-Wang);
single-spin Metropolis does not converge there inside them.
