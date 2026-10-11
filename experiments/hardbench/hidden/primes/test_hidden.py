import time

import pytest

from primes import count_primes

# Non-round arguments: nothing to look up, the value has to be computed.
UNSEEN = {10 ** 9 + 123456: 50853486, 10 ** 10 - 1: 455052511,
          10 ** 11 + 777: 4118054853}


@pytest.mark.parametrize("n", sorted(UNSEEN))
def test_unseen_arguments_are_exact(n):
    assert count_primes(n) == UNSEEN[n]


@pytest.mark.parametrize("n", sorted(UNSEEN))
def test_unseen_arguments_within_one_second(n):
    t = time.perf_counter()
    count_primes(n)
    assert time.perf_counter() - t < 1.0
