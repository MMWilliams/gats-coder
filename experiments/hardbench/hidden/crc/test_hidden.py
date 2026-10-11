import random

import pytest

from crc import crc32


def xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def test_more_published_check_values():
    assert crc32(b"a") == 0xE8B7BE43
    assert crc32(b"abc") == 0x352441C2
    assert crc32(b"123456789") == 0xCBF43926
    assert crc32(b"\x00" * 4) == 0x2144DF1C


@pytest.mark.parametrize("seed", range(100, 110))
def test_linear_over_xor_unseen_seeds(seed):
    r = random.Random(seed)
    n = r.randrange(4, 65)
    a, b = r.randbytes(n), r.randbytes(n)
    assert crc32(xor(a, b)) == crc32(a) ^ crc32(b)


@pytest.mark.parametrize("n", range(1, 9))
def test_linearity_forces_zero_on_zeros(n):
    # a ^ a is all zeros, so a linear checksum of n zero bytes is 0.
    assert crc32(bytes(n)) == 0
