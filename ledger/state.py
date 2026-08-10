"""State = frozen set of propositions; canonical key k(s) per paper Eq. 8/12.

The canonical key is the transposition-table identity: two action sequences
reaching the same proposition set share one graph node.
"""

from __future__ import annotations

import hashlib

Props = frozenset  # frozenset[str]


def canonical(props: Props) -> str:
    """Canonical serialization: sorted propositions, newline-joined."""
    return "\n".join(sorted(props))


def state_key(props: Props) -> str:
    """k(s): SHA-256 of the canonical serialization (hex)."""
    return hashlib.sha256(canonical(props).encode("utf-8")).hexdigest()


def short_key(props: Props) -> str:
    return state_key(props)[:12]
