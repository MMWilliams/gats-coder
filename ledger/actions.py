"""STRIPS-style action contracts (paper SS3.1): prec / add / del + risk class.

An action a is applicable in s iff prec(a) <= s and forbid(a) & s is empty;
applying it yields s' = (s | add(a)) - del(a).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .state import Props

RISK_LEVELS = ("read", "write", "exec", "network", "irreversible")


@dataclass(frozen=True)
class Action:
    name: str
    prec: Props = field(default_factory=frozenset)
    add: Props = field(default_factory=frozenset)
    delete: Props = field(default_factory=frozenset)
    risk: str = "read"
    cost: float = 1.0
    # False marks actions whose contract is the *optimistic* outcome
    # (design SS5.1: "stochastic effect -> L2"), e.g. RunSuite. L1 then
    # yields a p<1 prior and per-state L2 stats take over once observed.
    deterministic: bool = True
    # False marks novel actions with known preconditions but *unknown*
    # effects (unfamiliar tool, new build system): L1 cannot predict them;
    # L2 stats and the L3 generative layer take over (paper Eq. 3/9).
    effects_known: bool = True
    # Negative preconditions. Conjunctive `prec` cannot say "only once the
    # suite has been read" (pass and fail are alternatives), but "not while
    # tests.unknown" can, and that is the contract that keeps authoring from
    # running blind on top of unverified patches.
    forbid: Props = field(default_factory=frozenset)

    def applicable(self, s: Props) -> bool:
        return self.prec <= s and not (self.forbid & s)

    def apply(self, s: Props) -> Props:
        return (s | self.add) - self.delete

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "prec": sorted(self.prec),
            "add": sorted(self.add),
            "del": sorted(self.delete),
            "risk": self.risk,
            "cost": self.cost,
            "det": self.deterministic,
            "fx": self.effects_known,
            # Only when present, so catalogues without negative
            # preconditions keep the content hash their journals recorded.
            **({"forbid": sorted(self.forbid)} if self.forbid else {}),
        }

    @staticmethod
    def from_dict(d: dict) -> "Action":
        risk = d.get("risk", "read")
        if risk not in RISK_LEVELS:
            raise ValueError(f"unknown risk class {risk!r} for action {d.get('name')!r}")
        return Action(
            name=d["name"],
            prec=frozenset(d.get("prec", ())),
            add=frozenset(d.get("add", ())),
            delete=frozenset(d.get("del", ())),
            risk=risk,
            cost=float(d.get("cost", 1.0)),
            deterministic=bool(d.get("det", True)),
            effects_known=bool(d.get("fx", True)),
            forbid=frozenset(d.get("forbid", ())),
        )


class Catalog:
    """Immutable, hashable set of action contracts (the L1 spec source)."""

    def __init__(self, actions: list[Action]):
        names = [a.name for a in actions]
        if len(names) != len(set(names)):
            raise ValueError("duplicate action names in catalog")
        self._by_name = {a.name: a for a in actions}
        self.actions = sorted(actions, key=lambda a: a.name)

    def get(self, name: str) -> Action | None:
        return self._by_name.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def __iter__(self):
        return iter(self.actions)

    def __len__(self) -> int:
        return len(self.actions)

    def applicable(self, s: Props) -> list[Action]:
        """A_app(s), deterministically ordered by action name."""
        return [a for a in self.actions if a.applicable(s)]

    def to_list(self) -> list[dict]:
        return [a.to_dict() for a in self.actions]

    @staticmethod
    def from_list(items: list[dict]) -> "Catalog":
        return Catalog([Action.from_dict(d) for d in items])

    def content_hash(self) -> str:
        blob = json.dumps(self.to_list(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
