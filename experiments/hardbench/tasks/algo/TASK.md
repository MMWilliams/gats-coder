Implement `algo.py` in pure Python (standard library only) so that
`python -m pytest tests -q` passes within its time limits.

- `distinct_substrings(s: str) -> int`: the number of distinct non-empty
  substrings of `s`. Strings up to 300,000 characters, 15 s each.
- `inversions(a: list[int]) -> int`: the number of pairs i < j with
  a[i] > a[j]. Up to 1,500,000 elements, 15 s each.
- `nqueens(n: int) -> int`: the number of solutions of the n-queens
  problem. n = 14 within 60 s, n = 15 within 150 s.

The expected answers were computed by exact reference implementations on
seeded random inputs. The tests report only pass or fail; the algorithms
have to be the right ones.
