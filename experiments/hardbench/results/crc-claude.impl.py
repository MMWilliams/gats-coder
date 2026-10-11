"""Pure-Python CRC-32 (IEEE 802.3, reflected polynomial 0xEDB88320).

Bit-for-bit compatible with zlib.crc32, without importing zlib or binascii.

Example:
    >>> from crc import crc32
    >>> f"{crc32(b'hello'):08x}"
    '3610a686'
    >>> crc32(b"llo", crc32(b"he")) == crc32(b"hello")
    True

Command line:
    python -m crc [FILE ...]    # no FILE, or '-', reads stdin
"""

import sys

__all__ = ["crc32"]

_POLY = 0xEDB88320
_MASK = 0xFFFFFFFF


def _make_table():
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ _POLY if c & 1 else c >> 1
        table.append(c)
    return tuple(table)


_TABLE = _make_table()


def crc32(data, value=0):
    """Return the CRC-32 of bytes-like `data` as an int in [0, 2**32).

    `value` continues a running checksum, so crc32(b, crc32(a)) equals
    crc32(a + b). Like zlib, `value` is taken modulo 2**32.
    Raises TypeError if `data` isn't a bytes-like object.
    """
    if not isinstance(data, bytes):
        data = memoryview(data).tobytes()
    table = _TABLE
    crc = ~value & _MASK
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ _MASK


def _checksum_stream(f):
    value = 0
    for chunk in iter(lambda: f.read(1 << 16), b""):
        value = crc32(chunk, value)
    return value


def main(argv=None):
    """Print '<crc32 hex>  <path>' per file; return 1 if any file failed."""
    paths = sys.argv[1:] if argv is None else argv
    status = 0
    for path in paths or ["-"]:
        try:
            if path == "-":
                value = _checksum_stream(sys.stdin.buffer)
            else:
                with open(path, "rb") as f:
                    value = _checksum_stream(f)
        except OSError as e:
            print(f"crc: {path}: {e.strerror or e}", file=sys.stderr)
            status = 1
            continue
        print(f"{value:08x}  {path}")
    return status


if __name__ == "__main__":
    sys.exit(main())
