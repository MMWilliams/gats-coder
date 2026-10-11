Implement `sat.py`: a complete SAT solver in pure Python (standard library
only) fast enough that `python -m pytest tests -q` passes within its time
limits.

`solve(n_vars, clauses, time_limit=10.0) -> list[int] | None`

- `clauses` is a list of clauses in DIMACS style: each clause a list of
  non-zero ints, literal `v` for variable v true, `-v` for false, variables
  numbered 1..n_vars. Clauses may contain duplicate literals, tautologies
  (`v` and `-v` together) and may be empty (an empty clause is unsatisfiable).
  Not every variable has to appear.
- Satisfiable: return a complete assignment as a list of n_vars literals, one
  per variable in order (`v` or `-v`), that satisfies every clause.
- Unsatisfiable: return `None`.
- If the solver has not finished when `time_limit` seconds have elapsed,
  raise `TimeoutError`.

The test instances include random 3-SAT at the satisfiability threshold with
up to 150 variables and pigeonhole formulas. Plain DPLL without clause
learning does not finish those inside the limits; conflict-driven clause
learning with two watched literals and an activity-based decision heuristic
does, comfortably.
