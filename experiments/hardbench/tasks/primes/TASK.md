Implement `primes.py` in pure Python (standard library only) so that
`python -m pytest tests -q` passes within its time limits.

- `count_primes(n: int) -> int`: the number of primes less than or equal
  to `n`, exactly, for any `0 <= n <= 10**12`.

Every call in the suite, up to and including `n = 10**12`, must return
within one second on this machine. The expected values are exact; the suite
reports only pass or fail.
