Implement `factor.py`: complete factorization of univariate integer
polynomials over Z, from scratch in pure Python (standard library only;
the tests block sympy and mpmath), so that `python -m pytest tests -q`
passes within its time limits.

`factor(coeffs: list[int]) -> tuple[int, list[tuple[list[int], int]]]`

- `coeffs` are integer coefficients, highest degree first, like
  `[1, 0, -1]` for x^2 - 1. Leading zeros may be present. The zero
  polynomial raises `ValueError`.
- Returns `(content, factors)`: `content` is an integer carrying the sign and
  the gcd of the coefficients; each factor is `(coeffs, multiplicity)` with
  `coeffs` primitive, irreducible over Z, with positive leading coefficient
  and no leading zeros, and the list is sorted ascending by
  `(degree, coeffs)`. Constants give `(c, [])`. The identity
  `content * prod(factor^multiplicity) == input` must hold exactly.

The expected route is the standard one: squarefree decomposition, factoring
modulo a suitable prime (Berlekamp or Cantor-Zassenhaus), Hensel lifting to
a bound above Mignotte's, then recombining the lifted factors. The test set
includes Swinnerton-Dyer polynomials, which are irreducible over Z but split
into linear and quadratic factors modulo every prime, so trial-division
shortcuts and a single-prime guess do not get through.
