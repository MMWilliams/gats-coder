"""Apply a model's unified diff by locating its hunks in the file's content.

`git apply` trusts the hunk headers and exact context. Models get both wrong
while the change itself is right, so when git refuses, each hunk's old lines
are searched for in the file through a ladder of increasingly forgiving
matches (codex seek_sequence, aider search_replace, opencode block anchor;
see HARNESS_STUDY.md). The rebuilt files still go through
`diff_from_files` and `git apply --check`: this only finds where a change
goes, it never decides that a change is acceptable.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")
# Smart quotes, en/em dashes and nbsp -> ASCII. Code points, not literals:
# test_cli forbids non-ASCII string literals outside the UI fallbacks.
_PUNCT = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"',
          0x2013: "-", 0x2014: "-", 0x00A0: " "}


class LocateError(ValueError):
    """A hunk could not be placed; the message shows the nearest lines."""


@dataclass
class Hunk:
    start: int                                     # old start from the header
    ops: list = field(default_factory=list)        # [(" "|"-"|"+", text)]

    @property
    def old(self) -> list[str]:
        return [t for op, t in self.ops if op != "+"]

    @property
    def new(self) -> list[str]:
        return [t for op, t in self.ops if op != "-"]


@dataclass
class FilePatch:
    path: str
    new_file: bool = False
    deleted: bool = False
    hunks: list = field(default_factory=list)


def parse_diff(diff: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    hunk: Hunk | None = None
    lines = diff.splitlines()
    for i, line in enumerate(lines):
        # A header only when followed by +++: a removed "-- comment" line is
        # also spelled "--- ".
        if line.startswith("--- ") and i + 1 < len(lines) \
                and lines[i + 1].startswith("+++ "):
            old_path = line[4:].strip().split("\t")[0]
            new_path = lines[i + 1][4:].strip().split("\t")[0]
            path = new_path if new_path != "/dev/null" else old_path
            files.append(FilePatch(re.sub(r"^[ab]/", "", path),
                                   new_file=old_path == "/dev/null",
                                   deleted=new_path == "/dev/null"))
            hunk = None
            continue
        if hunk is None and line.startswith("+++ "):
            continue
        m = _HUNK.match(line)
        if m and files:
            hunk = Hunk(int(m.group(1)))
            files[-1].hunks.append(hunk)
            continue
        if hunk is None or line.startswith(("diff --git", "\\")):
            continue
        tag = line[:1]
        if tag in (" ", "-", "+"):
            hunk.ops.append((tag, line[1:]))
        elif line == "":
            hunk.ops.append((" ", ""))      # a blank context line, space dropped
    return files


_TIERS = (
    ("exact", lambda s: s),
    ("rstrip", str.rstrip),
    ("strip", str.strip),
    ("unicode", lambda s: unicodedata.normalize("NFKC", s).translate(_PUNCT).strip()),
)


def _find(lines: list[str], block: list[str], hint: int) -> tuple[int, int, str] | None:
    """(start, length, tier) of `block` in `lines`, nearest to `hint` first."""
    n = len(block)
    order = sorted(range(len(lines) - n + 1), key=lambda i: (abs(i - hint), i))
    for tier, f in _TIERS:
        want = [f(b) for b in block]
        have = [f(ln) for ln in lines]
        for i in order:
            if have[i:i + n] == want:
                return i, n, tier
    # Block anchor: first and last lines agree, the middle is >= 0.65 similar,
    # and the span is not wildly bigger than the block (opencode's guard).
    first, last = block[0].strip(), block[-1].strip()
    if n < 3 or not first or not last:
        return None
    best = None
    for i in order:
        if lines[i].strip() != first:
            continue
        for j in range(i + max(2, n // 2), min(len(lines), i + n * 3 // 2) + 1):
            if lines[j - 1].strip() != last:
                continue
            ratio = difflib.SequenceMatcher(
                None, "\n".join(x.strip() for x in lines[i:j]),
                "\n".join(x.strip() for x in block)).ratio()
            if ratio >= 0.65 and (best is None or ratio > best[0]):
                best = (ratio, i, j - i)
    return (best[1], best[2], "anchor") if best else None


def _indent(s: str) -> str:
    return s[:len(s) - len(s.lstrip(" \t"))]


def _replacement(hunk: Hunk, found: list[str], tier: str) -> list[str] | None:
    """The lines that replace `found`, or None if a removal is not there.
    Context comes from the file, never the model, so a forgiving match cannot
    smuggle in edits; added lines are re-indented the way the match was off."""
    if tier == "exact":
        return hunk.new
    # Line-for-line for the equal-length tiers; for the anchor only the
    # first line is a trustworthy pair.
    pairs = list(zip(hunk.old, found)) if tier != "anchor" else [(hunk.old[0], found[0])]
    shift = {_indent(o): _indent(f) for o, f in pairs if o.strip() and f.strip()}

    def added(text: str) -> str:
        ind = _indent(text)
        return shift.get(ind, ind) + text[len(ind):] if text.strip() else text

    if tier != "anchor":
        out, it = [], iter(found)
        for op, text in hunk.ops:
            if op == "+":
                out.append(added(text))
            else:
                line = next(it)
                if op == " ":
                    out.append(line)
        return out
    # Anchor: the middle drifted, so align by content. Every removal must be
    # found in the span; context that no longer matches is the file's to keep.
    out, pos = [], 0
    for op, text in hunk.ops:
        if op == "+":
            out.append(added(text))
            continue
        j = next((k for k in range(pos, len(found))
                  if found[k].strip() == text.strip()), None)
        if j is None:
            if op == "-":
                return None
            continue
        out.extend(found[pos:j])
        if op == " ":
            out.append(found[j])
        pos = j + 1
    out.extend(found[pos:])
    return out


def _nearest(lines: list[str], block: list[str]) -> str:
    probe = next((b.strip() for b in block if b.strip()), "")
    if not probe or not lines:
        return ""
    _, at = max(((difflib.SequenceMatcher(None, probe, ln.strip()).ratio(), -i)
                 for i, ln in enumerate(lines)))
    at = -at
    lo, hi = max(0, at - 4), min(len(lines), at + 5)
    return "\n".join(f"{k + 1}: {lines[k]}" for k in range(lo, hi))


def apply_by_content(root: Path, diff: str) -> tuple[list[tuple[str, str]], list[str]]:
    """((path, new content) per file, tiers used). Raises LocateError."""
    root = Path(root)
    out: list[tuple[str, str]] = []
    tiers: list[str] = []
    patches = parse_diff(diff)
    if not patches:
        raise LocateError("no file headers in the diff")
    for fp in patches:
        if fp.deleted:
            raise LocateError(f"{fp.path}: deletions are left to git")
        target = root / fp.path
        if fp.new_file or not target.is_file():
            if not fp.new_file:
                raise LocateError(f"{fp.path}: no such file")
            out.append((fp.path, "\n".join(ln for h in fp.hunks for ln in h.new) + "\n"))
            tiers.append("new")
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        cursor = 0
        for k, hunk in enumerate(fp.hunks, 1):
            # Blank context at the end is the usual thing models get wrong
            # (dropped, doubled), and it carries no position information.
            while hunk.ops and hunk.ops[-1][0] == " " and not hunk.ops[-1][1].strip():
                hunk.ops.pop()
            if not any(t.strip() for t in hunk.old):
                at = min(max(hunk.start, 0), len(lines))   # pure insertion
                lines[at:at] = hunk.new
                cursor = at + len(hunk.new)
                tiers.append("insert")
                continue
            found = _find(lines, hunk.old, max(cursor, hunk.start - 1))
            new = None
            if found is not None:
                start, length, tier = found
                new = _replacement(hunk, lines[start:start + length], tier)
            if new is None:
                near = _nearest(lines, hunk.old)
                raise LocateError(
                    f"{fp.path}: hunk {k} matches nothing in the file"
                    + (f"; the nearest lines are\n{near}" if near else ""))
            lines[start:start + length] = new
            cursor = start + len(new)
            tiers.append(tier)
        out.append((fp.path, "\n".join(lines) + ("\n" if text.endswith("\n") or not text else "")))
    return out, tiers
