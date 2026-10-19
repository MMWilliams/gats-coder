"""Append-only, hash-chained JSONL journal (design doc SS9.1).

Every entry carries prev = SHA-256 of the previous entry's canonical JSON;
h = SHA-256 of this entry's canonical JSON (computed without the h field).
Tampering with any byte breaks the chain from that point forward.
"""

from __future__ import annotations

import hashlib
import json
import os

GENESIS = "0" * 64


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _entry_hash(entry_without_h: dict) -> str:
    return hashlib.sha256(canonical(entry_without_h).encode("utf-8")).hexdigest()


def _scrubbed(value, redact):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrubbed(v, redact) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrubbed(v, redact) for v in value]
    return value


def runs(entries: list) -> list[list]:
    """A journal's runs, in order, one per HEADER.

    A file accumulates every run of an episode: resume appends a new
    segment file, but a re-run appends to this same one, and each starts
    with its own HEADER. Anything before the first HEADER is orphaned and
    dropped.
    """
    starts = [i for i, e in enumerate(entries) if e.get("t") == "HEADER"]
    if not starts:
        return [entries] if entries else []
    bounds = starts + [len(entries)]
    return [entries[a:b] for a, b in zip(starts, bounds[1:])]


def current_run(entries: list) -> list:
    """Just this run's entries.

    Scanning the whole file makes a SUCCESSFUL run report a stale
    escalation from an earlier attempt, which is worse than saying nothing.
    """
    found = runs(entries)
    return found[-1] if found else entries


class Journal:
    def __init__(self, path: str):
        self.path = path
        self._i = 0
        self._prev = GENESIS
        if os.path.exists(path) and os.path.getsize(path) > 0:
            entries = Journal.read(path)
            self._i = len(entries)
            self._prev = entries[-1]["h"]
        self._fh = open(path, "a", encoding="utf-8", newline="\n")

    def append(self, t: str, **payload) -> str:
        if "h" in payload:
            raise ValueError("'h' is a reserved journal field")
        # Journals are made to be shared (bug reports, audits, receipts), so
        # no credential is ever written into one -- chat text included.
        from .executor import redact_secrets
        payload = _scrubbed(payload, redact_secrets)
        entry = {"i": self._i, "t": t, "prev": self._prev, **payload}
        h = _entry_hash(entry)
        entry["h"] = h
        self._fh.write(canonical(entry) + "\n")
        self._fh.flush()
        self._i += 1
        self._prev = h
        return h

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def read(path: str) -> list[dict]:
        entries = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    @staticmethod
    def verify_entries(entries: list[dict]) -> tuple[bool, str | None]:
        """Recompute every hash and link over already-read entries.

        A prefix of a chain is itself a chain, so this also answers "were
        the first N entries of this journal exactly these" -- which is how
        a receipt stays verifiable after the episode it attests continues.
        Never mutates what it is given.
        """
        prev = GENESIS
        for idx, entry in enumerate(entries):
            body = {k: v for k, v in entry.items() if k != "h"}
            if body.get("i") != idx:
                return False, f"entry {idx}: index mismatch (got {body.get('i')})"
            if body.get("prev") != prev:
                return False, f"entry {idx}: broken link (prev hash mismatch)"
            actual = _entry_hash(body)
            if actual != entry.get("h"):
                return False, f"entry {idx}: content hash mismatch"
            prev = actual
        return True, None

    @staticmethod
    def verify_chain(path: str) -> tuple[bool, str | None]:
        """Recompute every hash and link. Returns (ok, error-description)."""
        return Journal.verify_entries(Journal.read(path))
