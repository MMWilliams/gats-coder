"""Chat sessions kept on disk, so `ledger -c` picks up where you left off.

Each session is a chat journal (hash-chained, secret-scrubbed like every
journal) under ~/.ledger/sessions/<project>/, outside the repository: a
transcript can quote code and decisions nobody meant to commit. The
directory is created owner-only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Session:
    id: str
    path: Path
    updated: float
    turns: int
    title: str

    def age(self) -> str:
        secs = max(0, time.time() - self.updated)
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if secs >= size:
                return f"{int(secs // size)}{unit} ago"
        return "just now"


def project_dir(repo: Path) -> Path:
    repo = Path(repo).resolve()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", repo.name).strip("-") or "root"
    digest = hashlib.sha256(str(repo).lower().encode()).hexdigest()[:8]
    return Path.home() / ".ledger" / "sessions" / f"{slug}-{digest}"


def new_path(repo: Path) -> Path:
    d = project_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)
    except OSError:                     # pragma: no cover
        pass
    sid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    path = d / f"{sid}.jsonl"
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, stat.S_IRUSR | stat.S_IWUSR))
    return path


def _entries(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _info(path: Path) -> Session | None:
    entries = _entries(path)
    users = [e for e in entries if e.get("t") in ("CHAT_USER", "EPISODE_LAUNCHED")]
    if not users:
        return None
    title = " ".join(str(users[0].get("text", "")).split())
    return Session(id=path.stem, path=path, updated=path.stat().st_mtime,
                   turns=len(users), title=title[:72])


def listing(repo: Path) -> list[Session]:
    """This project's sessions, most recent first (empty ones skipped)."""
    d = project_dir(repo)
    if not d.is_dir():
        return []
    found = [s for s in (_info(p) for p in d.glob("*.jsonl")) if s]
    return sorted(found, key=lambda s: s.updated, reverse=True)


def find(repo: Path, ident: str | None) -> Session | None:
    """The latest session, or the one whose id starts with `ident`, or the
    nth most recent when `ident` is a small number (as the picker shows)."""
    sessions = listing(repo)
    if not sessions:
        return None
    if not ident:
        return sessions[0]
    if ident.isdigit() and int(ident) <= len(sessions) and len(ident) < 4:
        return sessions[int(ident) - 1]
    # the full id, its start, or the short tag the banner shows (its end)
    return next((s for s in sessions
                 if s.id.startswith(ident) or s.id.endswith(ident)), None)


def restore(conv, path: Path) -> int:
    """Rebuild a Conversation's turns and summary from a session journal.
    Returns how many exchanges came back."""
    from .chat import Turn
    turns: list = []
    summary = ""
    for e in _entries(path):
        t = e.get("t")
        if t == "CHAT_USER":
            turns.append(Turn("user", str(e.get("text", ""))))
        elif t == "CHAT_ASSISTANT":
            turns.append(Turn("assistant", str(e.get("text", ""))))
        elif t == "EPISODE_LAUNCHED":
            turns.append(Turn("user", str(e.get("text", ""))))
        elif t == "EPISODE_RESULT" and e.get("summary"):
            turns.append(Turn("assistant", str(e["summary"])))
        elif t == "CHAT_SUMMARY":
            summary = str(e.get("text", ""))
            turns = turns[int(e.get("folded", 0)):]
    conv.turns = turns
    conv.summary = summary
    return sum(1 for t in turns if t.role == "user")
