"""Pure-Python CRC-32 (IEEE 802.3, reflected polynomial 0xEDB88320).

This module provides :func:`crc32`, a drop-in equivalent of ``zlib.crc32``
implemented using only the Python standard library (no ``zlib``, no
``binascii``).

Example
-------
>>> from crc import crc32
>>> crc32(b"123456789")
3421780262  # 0xCBF43926
"""

__all__ = ["crc32"]

_POLY = 0xEDB88320


def _make_table() -> tuple[int, ...]:
    """Build the 256-entry reflected CRC-32 lookup table."""
    table: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ _POLY
            else:
                crc >>= 1
        table.append(crc)
    return tuple(table)


_TABLE = _make_table()


def crc32(data: bytes) -> int:
    """Return the unsigned CRC-32 checksum of *data*.

    Parameters
    ----------
    data : bytes or bytearray
        The input message.

    Returns
    -------
    int
        A 32-bit unsigned integer (0 .. 2**32 - 1).

    Raises
    ------
    TypeError
        If *data* is not a ``bytes`` or ``bytearray`` instance.

    Notes
    -----
    * Polynomial: 0x04C11DB7 (reflected form 0xEDB88320)
    * Initial register: 0xFFFFFFFF
    * Final XOR: 0xFFFFFFFF
    * Check value: ``crc32(b"123456789") == 0xCBF43926``
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(
            f"crc32() argument must be bytes or bytearray, "
            f"not {type(data).__name__}"
        )

    crc = 0xFFFFFFFF
    table = _TABLE
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF
