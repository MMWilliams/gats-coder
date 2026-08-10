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


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _entry_hash(entry_without_h: dict) -> str:
    return hashlib.sha256(_canonical(entry_without_h).encode("utf-8")).hexdigest()


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
        entry = {"i": self._i, "t": t, "prev": self._prev, **payload}
        if "h" in payload:
            raise ValueError("'h' is a reserved journal field")
        h = _entry_hash(entry)
        entry["h"] = h
        self._fh.write(_canonical(entry) + "\n")
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
    def verify_chain(path: str) -> tuple[bool, str | None]:
        """Recompute every hash and link. Returns (ok, error-description)."""
        prev = GENESIS
        for idx, entry in enumerate(Journal.read(path)):
            claimed = entry.pop("h", None)
            if entry.get("i") != idx:
                return False, f"entry {idx}: index mismatch (got {entry.get('i')})"
            if entry.get("prev") != prev:
                return False, f"entry {idx}: broken link (prev hash mismatch)"
            actual = _entry_hash(entry)
            if actual != claimed:
                return False, f"entry {idx}: content hash mismatch"
            prev = actual
        return True, None
