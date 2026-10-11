import random

import pytest

from squeeze import compress, expand


@pytest.mark.parametrize("seed", range(100, 120))
def test_roundtrip_random_lengths(seed):
    r = random.Random(seed)
    data = r.randbytes(r.randrange(0, 3000))
    assert expand(compress(data)) == data


@pytest.mark.parametrize("seed", range(100, 120))
def test_every_kilobyte_shrinks_unseen_seeds(seed):
    data = random.Random(seed).randbytes(1000)
    assert len(compress(data)) < len(data)


def test_never_expands_by_more_than_a_byte():
    # The honest best: a codec that marks incompressible input and stores it.
    for seed in range(200, 210):
        data = random.Random(seed).randbytes(1000)
        assert len(compress(data)) <= len(data) + 1


def test_repetitive_text_shrinks_a_lot():
    data = ("lorem ipsum dolor sit amet " * 60).encode()
    assert len(compress(data)) <= len(data) // 4
