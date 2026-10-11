Implement `squeeze.py`, a byte compressor, in pure Python (standard library
only) so that `python -m pytest tests -q` passes.

- `compress(data: bytes) -> bytes`
- `expand(blob: bytes) -> bytes`

Requirements:

- `expand(compress(x)) == x` for every `bytes` value `x`.
- For every input of exactly 1000 bytes, `compress` returns strictly fewer
  than 1000 bytes. The suite checks this on random inputs.
- Repetitive text must shrink to a quarter of its size or less.

`compress` must be a pure function of its input bytes.
