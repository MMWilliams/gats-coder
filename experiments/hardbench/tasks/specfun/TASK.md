Implement `specfun.py`: special functions in pure Python, accurate to
near machine precision across their whole ranges, so that
`python -m pytest tests -q` passes.

Allowed: the `math` module for `sqrt, exp, log, log1p, expm1, sin, cos,
atan, pi, e, floor, fabs, factorial, isinf, isnan`. Forbidden: `math.erf`,
`math.erfc`, `math.gamma`, `math.lgamma`, and any of numpy, scipy, mpmath,
sympy (the tests block them). The point is the algorithms.

- `erf(x)`, `erfc(x)` for any real x. erfc must keep full relative accuracy
  in the tail (erfc(26) ~ 5e-296 is not 0).
- `lgamma(x)`: ln|Gamma(x)| for real x that is not a non-positive integer,
  including negative x.
- `j0(x)`, `j1(x)` for any real x; `y0(x)` for x > 0. Bessel functions of
  the first and second kind, 0 <= |x| <= 100.
- `gammainc_p(a, x)`: the regularised lower incomplete gamma P(a, x) for
  a > 0, x >= 0, including large a (up to 1000) and x near a.

Accuracy, checked against mpmath at 30 digits: for erf, j0, j1, y0,
gammainc_p and lgamma the error must be <= 1e-12 * max(1, |true value|);
for erfc the relative error must be <= 1e-12 wherever the true value is
above 1e-300.
