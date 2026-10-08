"""Ed25519 out of the standard library, and the key that signs receipts.

A receipt is only worth something to somebody who does not trust the machine
that produced it, and that rules out HMAC: a verifier holding the secret
could have forged the thing it is checking. The signature has to be
public-key, and LEDGER has no dependencies to spend on one -- hence the
RFC 8032 reference construction below. It is slow (a few hundred
milliseconds per operation) and that is fine: signing happens once per
episode, not once per step.

The private seed lives under the user's home directory at 0600, never in
the repo, for the same reason API keys do -- see credentials.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

# --- RFC 8032 reference construction -----------------------------------
# Structure kept verbatim from the RFC's appendix so it can be diffed
# against the specification. Do not "optimise" it in place.

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _sha512_modq(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little") % _Q


def _modp_inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _modp_inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _modp_inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_G_Y = 4 * _modp_inv(5) % _P
_G_X = _recover_x(_G_Y, 0)
_G = (_G_X, _G_Y, 1, _G_X * _G_Y % _P)


def _point_add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f, g * h, f * g, e * h)


def _point_mul(s: int, p):
    q = (0, 1, 1, 0)                    # neutral element
    while s > 0:
        if s & 1:
            q = _point_add(q, p)
        p = _point_add(p, p)
        s >>= 1
    return q


def _point_equal(p, q) -> bool:
    if (p[0] * q[2] - q[0] * p[2]) % _P != 0:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _point_compress(p) -> bytes:
    zinv = _modp_inv(p[2])
    x = p[0] * zinv % _P
    y = p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _point_decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _secret_expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _secret_expand(seed)
    return _point_compress(_point_mul(a, _G))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _secret_expand(seed)
    pub = _point_compress(_point_mul(a, _G))
    r = _sha512_modq(prefix + msg)
    rs = _point_compress(_point_mul(r, _G))
    h = _sha512_modq(rs + pub + msg)
    s = (r + h * a) % _Q
    return rs + int.to_bytes(s, 32, "little")


def verify(pub: bytes, msg: bytes, signature: bytes) -> bool:
    if len(pub) != 32 or len(signature) != 64:
        return False
    a = _point_decompress(pub)
    if a is None:
        return False
    rs = signature[:32]
    r = _point_decompress(rs)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _sha512_modq(rs + pub + msg)
    return _point_equal(_point_mul(s, _G), _point_add(r, _point_mul(h, a)))


# --- the signing key on disk -------------------------------------------

ALG = "ed25519"


def key_id(pub: bytes) -> str:
    """A short, stable name for a public key. Counterparties pin this."""
    return hashlib.sha256(pub).hexdigest()[:16]


def keys_dir() -> Path:
    return Path.home() / ".ledger" / "keys"


def key_path(name: str = "default") -> Path:
    return keys_dir() / f"{name}.json"


def load_key(name: str = "default", *, create: bool = True) -> dict:
    """The signing key, generated on first use.

    Returns {name, alg, seed (hex), public (hex), key_id}. The seed never
    leaves this file; everything published carries only `public`.
    """
    path = key_path(name)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        seed = bytes.fromhex(data["seed"])
        pub = public_key(seed)
        if data.get("public") != pub.hex():
            raise ValueError(f"key {path} is inconsistent: public key does "
                             "not derive from the stored seed")
        return data
    if not create:
        raise FileNotFoundError(path)
    seed = os.urandom(32)
    pub = public_key(seed)
    data = {"name": name, "alg": ALG, "seed": seed.hex(),
            "public": pub.hex(), "key_id": key_id(pub)}
    path.parent.mkdir(parents=True, exist_ok=True)
    # 0600 at creation, not after: opening 0644 and chmod-ing later leaves a
    # window in which the signing seed is world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:                     # pragma: no cover - exotic filesystems
        pass
    return data
