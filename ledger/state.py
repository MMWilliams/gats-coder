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


# LEDGER's own bookkeeping: which patch slots ran, the budget meter, the
# episode branch. They say nothing about what an action does, but they make
# every round's states unique, so a cache keyed on k(s) never hits across
# rounds. Measured on the chess loop: 1,960 LLM planning calls in six hours,
# about 45% of wall time, re-asking the same question about ReviewDesign.
BOOKKEEPING_PREFIXES = ("patch.queued:", "patch.applied:", "budget.",
                        "repo.branch:")


def effect_key(props: Props) -> str:
    """k(s) with bookkeeping removed: the identity for questions about an
    action's *effect*, which bookkeeping cannot change."""
    return state_key(frozenset(p for p in props
                               if not p.startswith(BOOKKEEPING_PREFIXES)))


def short_key(props: Props) -> str:
    return state_key(props)[:12]
