import time

import pytest

from primes import count_primes

SMALL = {0: 0, 1: 0, 2: 1, 3: 2, 10: 4, 100: 25, 1000: 168, 10 ** 6: 78498}
LARGE = {10 ** 8: 5761455, 10 ** 9: 50847534, 10 ** 10: 455052511,
         10 ** 11: 4118054813, 10 ** 12: 37607912018}


@pytest.mark.parametrize("n", sorted(SMALL))
def test_small(n):
    assert count_primes(n) == SMALL[n]


def test_matches_sieve_below_20000():
    s = bytearray([1]) * 20001
    s[0] = s[1] = 0
    for i in range(2, 142):
        if s[i]:
            s[i * i::i] = bytearray(len(s[i * i::i]))
    acc = 0
    for n in range(20001):
        acc += s[n]
        if n % 997 == 0:
            assert count_primes(n) == acc


@pytest.mark.parametrize("n", sorted(LARGE))
def test_large_within_one_second(n):
    t = time.perf_counter()
    got = count_primes(n)
    dt = time.perf_counter() - t
    assert got == LARGE[n]
    assert dt < 1.0, dt
