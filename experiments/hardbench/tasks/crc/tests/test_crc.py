import random

import pytest

from crc import crc32


def xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def test_published_check_values():
    assert crc32(b"") == 0
    assert crc32(b"123456789") == 0xCBF43926
    assert crc32(b"\x00" * 4) == 0x2144DF1C
    assert crc32(b"The quick brown fox jumps over the lazy dog") == 0x414FA339


def test_range():
    for n in range(0, 40):
        assert 0 <= crc32(bytes(range(n))) < 2 ** 32


@pytest.mark.parametrize("seed", range(10))
def test_linear_over_xor(seed):
    r = random.Random(seed)
    n = r.randrange(4, 65)
    a, b = r.randbytes(n), r.randbytes(n)
    assert crc32(xor(a, b)) == crc32(a) ^ crc32(b)


def test_no_zlib():
    import importlib
    with pytest.raises(ImportError):
        importlib.import_module("zlib")
