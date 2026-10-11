import random
import time

import pytest

from algo import distinct_substrings, inversions, nqueens


def text(n, alphabet, seed):
    rng = random.Random(seed)
    return "".join(rng.choice(alphabet) for _ in range(n))


def array(n, seed, lo=0, hi=10 ** 9):
    rng = random.Random(seed)
    return [rng.randint(lo, hi) for _ in range(n)]


def timed(fn, *args, limit):
    t = time.time()
    out = fn(*args)
    assert time.time() - t < limit, f"took {time.time() - t:.1f}s, limit {limit}s"
    return out


def test_distinct_substrings_small():
    assert distinct_substrings("") == 0
    assert distinct_substrings("a") == 1
    assert distinct_substrings("aaaa") == 4
    assert distinct_substrings("abab") == 7
    assert distinct_substrings("banana") == 15


@pytest.mark.parametrize("n,alphabet,seed,expected", [
    (200_000, "ab", 7, 19996799851),
    (200_000, "abcdefghijklmnopqrstuvwxyz", 8, 19999478267),
])
def test_distinct_substrings_large(n, alphabet, seed, expected):
    assert timed(distinct_substrings, text(n, alphabet, seed), limit=15) == expected


def test_inversions_small():
    assert inversions([]) == 0
    assert inversions([1, 2, 3]) == 0
    assert inversions([3, 2, 1]) == 3
    assert inversions([2, 2, 1, 1]) == 4
    assert inversions([5, -1, 5, 0]) == 3


def test_inversions_large():
    assert timed(inversions, array(1_000_000, 7), limit=15) == 249915642076


@pytest.mark.parametrize("n,expected", [(1, 1), (2, 0), (4, 2), (6, 4), (8, 92), (10, 724)])
def test_nqueens_small(n, expected):
    assert nqueens(n) == expected


def test_nqueens_12():
    assert timed(nqueens, 12, limit=20) == 14200


def test_nqueens_14():
    assert timed(nqueens, 14, limit=60) == 365596
