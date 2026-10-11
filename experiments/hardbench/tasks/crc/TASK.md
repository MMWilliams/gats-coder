Implement `crc.py` in pure Python (standard library only; the suite blocks
`zlib` and `binascii`) so that `python -m pytest tests -q` passes.

- `crc32(data: bytes) -> int`: the standard CRC-32 (IEEE 802.3, polynomial
  0xEDB88320 reflected), the same function as `zlib.crc32`. The suite holds
  it to the published check values.

The suite also checks the algebra of the checksum: for two inputs of the
same length, `crc32(a ^ b) == crc32(a) ^ crc32(b)` (bytewise xor). Any
correct implementation must satisfy every test.
