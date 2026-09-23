"""Layered world model W(s, a) -> (s', p, layer) per paper SS3.2 (Eqs. 1-6).

Layer order (Eq. 5): L1 symbolic -> L2 learned -> L3 generative.

L1 owns every *deterministic, effect-known* action with a catalog contract:
for a known action with an unmet precondition it confidently predicts
failure (s, 0.0) rather than falling through.

Actions marked deterministic=False (design SS5.1: "stochastic effect -> L2",
e.g. RunSuite) carry an *optimistic* contract: per-state L2 stats take
precedence once observed; before any observation the contract effect is
returned with a p<1 prior, so the Eq. 17 lambda-penalty prices in the
uncertainty.

Actions marked effects_known=False (novel tools) keep symbolic precondition
gating but get their effects from L2 stats or the L3 generative layer,
whose predictions are cached on (k(s), a) per Eq. 8 -- so LLM planning
calls are bounded by *distinct novel transitions* (Eq. 9) and fall toward
zero as the cache and L2 fill (paper Table 4's bootstrapping phase).

L2 is keyed by (k(s), action) -- the same keying as the original gats2
LearnedDynamics and the Eq. 8 cache. It persists to SQLite and can be
populated by mining past episode journals (design SS6.2).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass

from .actions import Catalog
from .journal import Journal
from .state import Props, effect_key, state_key

STOCHASTIC_PRIOR = 0.5  # Eq. 3's maximum-entropy prior, reused for unobserved
                        # optimistic contracts

L3_PROMPT = """You are the transition model of a planning agent. Given the \
current state (a set of propositions) and an action, predict the action's \
effect.

State propositions:
{props}

Action: {action}

Respond with ONLY a JSON object: {{"add": [<propositions added>], \
"del": [<propositions removed>]}}. Use only short proposition strings in \
the style of the state above."""


@dataclass(frozen=True)
class Prediction:
    next_props: Props
    p: float
    layer: str  # "L1" | "L2" | "L3" | "none"


class L1Symbolic:
    """Eq. 1: exact STRIPS matching. p=1 if applicable, else p=0 (same state)."""

    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def predict(self, s: Props, action_name: str) -> Prediction:
        a = self.catalog.get(action_name)
        if a is None:  # pragma: no cover - callers gate on catalog membership
            return Prediction(s, 0.0, "L1")
        if not a.applicable(s):
            return Prediction(s, 0.0, "L1")
        p = 1.0 if a.deterministic else STOCHASTIC_PRIOR
        return Prediction(a.apply(s), p, "L1")


class L2Learned:
    """Eq. 2/6: most-frequent observed effect at (k(s), a), confidence
    min(n/n0, 1). Effects are (added, removed) deltas. SQLite-persistent."""

    def __init__(self, n0: int = 10):
        self.n0 = n0
        self._effects: dict[tuple[str, str], Counter] = {}

    def record(self, s: Props, action_name: str, s_next: Props) -> None:
        added = tuple(sorted(s_next - s))
        removed = tuple(sorted(s - s_next))
        key = (state_key(s), action_name)
        self._effects.setdefault(key, Counter())[(added, removed)] += 1

    def observations_at(self, s: Props, action_name: str) -> int:
        return sum(self._effects.get((state_key(s), action_name), Counter()).values())

    def observations(self, action_name: str) -> int:
        """Total observations of an action across all states."""
        return sum(
            sum(c.values()) for (_, a), c in self._effects.items() if a == action_name
        )

    def can_predict(self, s: Props, action_name: str) -> bool:
        return self.observations_at(s, action_name) > 0

    def predict(self, s: Props, action_name: str) -> Prediction:
        effects = self._effects[(state_key(s), action_name)]
        # Deterministic argmax: highest count, then lexicographic effect key.
        (added, removed), _ = max(
            effects.items(), key=lambda kv: (kv[1], tuple(map(str, kv[0])))
        )
        s_next = (s | frozenset(added)) - frozenset(removed)
        conf = min(self.observations_at(s, action_name) / self.n0, 1.0)
        return Prediction(s_next, conf, "L2")

    _SCHEMA = """CREATE TABLE IF NOT EXISTS l2_stats (
        state_key TEXT, action TEXT, added TEXT, removed TEXT, count INTEGER,
        PRIMARY KEY (state_key, action, added, removed))"""

    def save(self, path: str) -> None:
        con = sqlite3.connect(path)
        try:
            con.execute(self._SCHEMA)
            con.execute("DELETE FROM l2_stats")
            con.executemany(
                "INSERT INTO l2_stats VALUES (?,?,?,?,?)",
                [(k, a, json.dumps(list(add)), json.dumps(list(rem)), n)
                 for (k, a), counter in self._effects.items()
                 for (add, rem), n in counter.items()],
            )
            con.commit()
        finally:
            con.close()

    def load(self, path: str) -> None:
        con = sqlite3.connect(path)
        try:
            con.execute(self._SCHEMA)
            for k, a, add, rem, n in con.execute("SELECT * FROM l2_stats"):
                effect = (tuple(json.loads(add)), tuple(json.loads(rem)))
                self._effects.setdefault((k, a), Counter())[effect] += n
        finally:
            con.close()


class L3Stub:
    """No-backend placeholder: counts the calls that *would* hit an LLM."""

    def __init__(self):
        self.calls = 0
        self.cache: dict[tuple[str, str], Prediction] = {}

    def predict(self, s: Props, action_name: str,
                vocabulary: frozenset | None = None) -> Prediction | None:
        key = (effect_key(s), action_name)
        if key in self.cache:
            return self.cache[key]
        self.calls += 1
        return None

    def save(self, path: str) -> None:  # interface parity with L3Generative
        pass


class L3Generative:
    """Eq. 3: LLM effect prediction at fixed p=0.5, cached on (k(s), a)
    (Eq. 8). Cache hits are free; `calls` counts real backend invocations
    -- the number the paper's Eq. 9 bounds and Table 4 drives to zero."""

    def __init__(self, backend, cache_path: str | None = None):
        self.backend = backend
        self.calls = 0
        self.cache: dict[tuple[str, str], tuple[tuple, tuple]] = {}
        if cache_path:
            self.load(cache_path)

    def predict(self, s: Props, action_name: str,
                vocabulary: frozenset | None = None) -> Prediction | None:
        key = (effect_key(s), action_name)
        if key in self.cache:
            added, removed = self.cache[key]
            return Prediction((s | frozenset(added)) - frozenset(removed),
                              STOCHASTIC_PRIOR, "L3")
        prompt = L3_PROMPT.format(
            props="\n".join(f"- {p}" for p in sorted(s)) or "- (empty)",
            action=action_name,
        )
        self.calls += 1
        from .llm import extract_json
        try:
            obj = extract_json(self.backend.complete(prompt))
        except Exception:
            return None
        if obj is None:
            return None
        added = tuple(sorted(str(p) for p in obj.get("add", ())))
        removed = tuple(sorted(str(p) for p in obj.get("del", ())))
        if vocabulary is not None:
            # A proposition no sensor can report ("design.reviewed") is a
            # guess the planner can never confirm; dropping it makes the
            # prediction an honest no-op instead of a phantom state.
            added = tuple(p for p in added if p in vocabulary)
            removed = tuple(p for p in removed if p in vocabulary)
        self.cache[key] = (added, removed)
        return Prediction((s | frozenset(added)) - frozenset(removed),
                          STOCHASTIC_PRIOR, "L3")

    _SCHEMA = """CREATE TABLE IF NOT EXISTS l3_cache (
        state_key TEXT, action TEXT, added TEXT, removed TEXT,
        PRIMARY KEY (state_key, action))"""

    def save(self, path: str) -> None:
        con = sqlite3.connect(path)
        try:
            con.execute(self._SCHEMA)
            con.execute("DELETE FROM l3_cache")
            con.executemany(
                "INSERT INTO l3_cache VALUES (?,?,?,?)",
                [(k, a, json.dumps(list(add)), json.dumps(list(rem)))
                 for (k, a), (add, rem) in self.cache.items()],
            )
            con.commit()
        finally:
            con.close()

    def load(self, path: str) -> None:
        con = sqlite3.connect(path)
        try:
            con.execute(self._SCHEMA)
            for k, a, add, rem in con.execute("SELECT * FROM l3_cache"):
                self.cache[(k, a)] = (tuple(json.loads(add)),
                                      tuple(json.loads(rem)))
        finally:
            con.close()


class LayeredWorldModel:
    """Layer-selection rule of Eq. 5."""

    def __init__(self, catalog: Catalog, l3=None):
        self.catalog = catalog
        self.l1 = L1Symbolic(catalog)
        self.l2 = L2Learned()
        self.l3 = l3 if l3 is not None else L3Stub()

    def predict(self, s: Props, action_name: str) -> Prediction:
        a = self.catalog.get(action_name)
        if a is not None and not a.applicable(s):
            # Preconditions are symbolic even when effects aren't: certain no-op.
            return Prediction(s, 0.0, "L1")
        if a is not None and a.effects_known:
            if a.deterministic:
                observed = self._contradicted(s, action_name)
                if observed is not None:
                    return observed
                return self.l1.predict(s, action_name)
            # Stochastic contract: learned per-state stats first (Eq. 5),
            # optimistic L1 prior before any observation.
            if self.l2.can_predict(s, action_name):
                return self.l2.predict(s, action_name)
            return self.l1.predict(s, action_name)
        # Unknown effects (or unknown action): L2 -> L3 -> none.
        if self.l2.can_predict(s, action_name):
            return self.l2.predict(s, action_name)
        pred = self.l3.predict(s, action_name, self._vocabulary())
        if pred is not None:
            return pred
        return Prediction(s, 0.0, "none")

    def _vocabulary(self) -> frozenset:
        # Recomputed per call: the strategist can grow the catalog mid-episode.
        vocab: set = set()
        for a in self.catalog:
            vocab |= a.prec | a.add | a.delete
        return frozenset(vocab)

    def _contradicted(self, s: Props, action_name: str) -> Prediction | None:
        """The observed effect of a deterministic action whose contract was
        falsified here -- or None while the contract still holds.

        A contract is a claim about reality, and reality gets the last word.
        Without this, an ApplyPatch whose diff no longer applies kept
        predicting success: the planner pruned no self-loop, re-selected it,
        watched it fail, and did that fifteen times until the step cap. The
        divergence was recorded to L2 on every one of them and never read.
        For a deterministic action a single observation settles the effect
        at this state, so it is returned at the contract's own confidence --
        the effect was falsified, not the determinism.
        """
        if not self.l2.can_predict(s, action_name):
            return None
        observed = self.l2.predict(s, action_name)
        contract = self.l1.predict(s, action_name)
        if state_key(observed.next_props) == state_key(contract.next_props):
            return None
        return Prediction(observed.next_props, contract.p, "L2")

    def record_outcome(self, s: Props, action_name: str, s_next: Props) -> None:
        """Feed observed transitions to L2 (the divergence-loop learning hook)."""
        self.l2.record(s, action_name, s_next)


def mine_journal(path: str, l2: L2Learned) -> int:
    """Replay a journal's PLAN/OBSERVE stream into L2 (design SS6.2: L2 is
    'transition stats mined from execution journals'). Returns the number of
    transitions recorded. State provenance: HEADER.initial, then each
    OBSERVE.obs_props."""
    entries = Journal.read(path)
    if not entries or entries[0]["t"] != "HEADER":
        raise ValueError(f"{path}: not an episode journal")
    props: Props = frozenset(entries[0]["initial"])
    pending: str | None = None
    mined = 0
    for e in entries[1:]:
        if e["t"] == "PLAN":
            pending = e["chosen"]
        elif e["t"] == "OBSERVE" and pending is not None:
            obs = frozenset(e["obs_props"])
            l2.record(props, pending, obs)
            mined += 1
            props = obs
            pending = None
    return mined
