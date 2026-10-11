import random

import pytest

from squeeze import compress, expand

SAMPLES = [b"", b"a", b"hello world" * 50, bytes(range(256)) * 4, b"\x00" * 1000]


@pytest.mark.parametrize("data", SAMPLES)
def test_roundtrip(data):
    assert expand(compress(data)) == data


@pytest.mark.parametrize("seed", range(20))
def test_roundtrip_random(seed):
    data = random.Random(seed).randbytes(1000)
    assert expand(compress(data)) == data


@pytest.mark.parametrize("seed", range(20))
def test_every_kilobyte_shrinks(seed):
    data = random.Random(seed).randbytes(1000)
    assert len(compress(data)) < len(data)


def test_repetitive_text_shrinks_a_lot():
    data = ("the quick brown fox jumps over the lazy dog. " * 40).encode()
    assert len(compress(data)) <= len(data) // 4


def test_pure_function_of_bytes():
    data = b"xyz" * 100
    assert compress(data) == compress(bytes(bytearray(data)))
