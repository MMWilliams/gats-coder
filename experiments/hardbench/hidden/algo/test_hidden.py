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


@pytest.mark.parametrize("n,alphabet,seed,expected", [
    (300_000, "abc", 9, 44996970457),
    (250_000, "ab", 10, 31245919297),
])
def test_distinct_substrings_unseen(n, alphabet, seed, expected):
    assert timed(distinct_substrings, text(n, alphabet, seed), limit=15) == expected


def test_distinct_substrings_structured():
    assert distinct_substrings("a" * 5000) == 5000
    assert distinct_substrings("ab" * 2500) == 2 * 5000 - 1
    s = "".join(chr(97 + (i * i) % 26) for i in range(1500))
    brute = len({s[i:j] for i in range(len(s)) for j in range(i + 1, len(s) + 1)})
    assert distinct_substrings(s) == brute


@pytest.mark.parametrize("n,seed,expected", [
    (1_000_000, 8, 249838101222),
    (1_500_000, 9, 562454167346),
])
def test_inversions_unseen(n, seed, expected):
    assert timed(inversions, array(n, seed), limit=15) == expected


def test_inversions_with_many_duplicates_and_negatives():
    a = array(500_000, 11, -5, 5)
    b = a[:3000]
    assert inversions(b) == sum(1 for i in range(len(b)) for j in range(i + 1, len(b)) if b[i] > b[j])
    assert timed(inversions, a, limit=15) == 56750152779


def test_inversions_sorted_and_reversed():
    n = 200_000
    assert inversions(list(range(n))) == 0
    assert inversions(list(range(n, 0, -1))) == n * (n - 1) // 2


def test_nqueens_13():
    assert timed(nqueens, 13, limit=60) == 73712


def test_nqueens_15():
    assert timed(nqueens, 15, limit=150) == 2279184
