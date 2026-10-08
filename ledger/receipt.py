"""A portable, signed account of what the agent was allowed to do and did.

The journal is already the whole truth: hash-chained, replayable, and
sitting on the machine that produced it. That last part is the problem. A
counterparty who wants to know what your agent did on their behalf cannot
be handed a directory of blobs and a promise, and would have no reason to
believe it if they were.

A receipt is the journal's *claims* -- policy in force, every gated
decision, every action executed, every false claim the sensors caught, and
what was in scope that nobody vouched for -- extracted into one file and
signed with a key the issuer publishes. It is small enough to email and
carries each segment's head hash, so it commits to the exact run without
containing it. Verification comes in two strengths:

  signature only   the issuer stands behind these claims
  with the journal the claims are a faithful extract of that chain

The second is the one that matters, and it is why the body is a pure
function of the journal bytes: a verifier re-derives it and compares.
Nothing in a receipt is asserted by hand.

The unit is the EPISODE, not the file. Resume writes episode.jsonl,
episode.r2.jsonl, ...; a re-run appends another HEADER to the file it is
already in. A receipt for the first attempt alone would be true and would
quietly omit the attempt that did the work, which is the sort of honest
document nobody should accept.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import signing
from .executor import GATED_RISKS, redact_secrets
from .journal import Journal, canonical, current_run, runs
from .taint import TAINT_RISKS, external_sources

SCHEMA = "ledger.receipt/2"

_SEGMENT = re.compile(r"^(?P<base>.+?)(?:\.r(?P<n>\d+))?\.jsonl$")


def segment_paths(source: str | Path) -> list[Path]:
    """Every journal segment of one episode, in the order they were written.

    A directory means the episode in it; a file means the episode that file
    belongs to, whichever segment was named.
    """
    source = Path(source)
    if source.is_dir():
        directory, base = source, "episode"
    else:
        directory = source.parent
        match = _SEGMENT.match(source.name)
        base = match.group("base") if match else source.stem

    found: list[tuple[int, Path]] = []
    first = directory / f"{base}.jsonl"
    if first.is_file():
        found.append((1, first))
    for path in directory.glob(f"{base}.r*.jsonl"):
        match = _SEGMENT.match(path.name)
        if match and match.group("n"):
            found.append((int(match.group("n")), path))
    found.sort(key=lambda pair: pair[0])
    return [path for _, path in found] or [source]


class _Scope:
    """Untrusted content in scope during one run.

    Per run, not per receipt: a resumed episode is a new process with a new
    context, and the page the previous attempt read is not in it. What does
    carry across is whatever the HEADER says it inherited. `seen` is the
    receipt-wide union, which answers a different question -- what did this
    task ever touch.
    """

    def __init__(self, seen: list[str]):
        self.sources: list[str] = []
        self.first_at: int | None = None
        self._seen = seen

    def note(self, sources, at) -> None:
        for source in sources:
            if source not in self.sources:
                self.sources.append(source)
            if source not in self._seen:
                self._seen.append(source)
            if self.first_at is None:
                self.first_at = at


def _commands(entry: dict) -> list[dict]:
    """Command records, with the argv line redacted.

    Blob content is redacted at the BlobStore boundary, but the command
    *line* never passed through it, and a token handed to curl on the
    command line is a token in the receipt otherwise.
    """
    out = []
    for command in entry.get("commands") or []:
        if not isinstance(command, dict):
            continue
        out.append({"cmd": redact_secrets(str(command.get("cmd", ""))),
                    "exit": command.get("exit"),
                    "out": command.get("out")})
    return out


def _build(loaded: list[tuple[Path, list]], scope: str) -> dict:
    """The signable body. Pure in the strict sense: same entries in, same
    bytes out, on any machine, at any version. Verification depends on it."""
    segments, stream = [], []
    for index, (path, entries) in enumerate(loaded):
        if not entries:
            raise ValueError(f"{path}: empty journal, nothing to attest")
        chain_ok, chain_error = Journal.verify_entries(entries)
        found = runs(entries) if scope == "episode" else [current_run(entries)]
        segments.append({"file": path.name, "entries": len(entries),
                         "runs": len(found), "head": entries[-1].get("h"),
                         "chain_ok": chain_ok, "chain_error": chain_error})
        for run in found:
            stream.append((index, run))

    summaries, decisions, actions, incidents = [], [], [], []
    seen: list[str] = []
    from_ingress: set[str] = set()
    privileged, gaps = [], []

    for number, (segment, entries) in enumerate(stream):
        header = entries[0] if entries[0].get("t") == "HEADER" else {}
        end = next((e for e in reversed(entries) if e.get("t") == "END"), {})
        escalated = next((e for e in reversed(entries)
                          if e.get("t") == "ESCALATION"), None)
        in_scope = _Scope(seen)

        for entry in entries:
            index = entry.get("i")
            kind = entry.get("t")
            # Two independent records of the same fact, both inside the
            # chain: what the run read (ingress entries, HEADER inheritance
            # included) and what the gate believed when it decided. Neither
            # subsumes the other -- an ingress path nobody taught the gate
            # about shows up only in the first, a provenance handed down by
            # a conversation only in the second.
            ingress = external_sources(entry)
            from_ingress.update(ingress)
            in_scope.note(ingress, index)

            if kind == "GATE":
                recorded = [str(s) for s in (entry.get("tainted") or [])]
                # Checked against what was known BEFORE this entry: a gate
                # that decided with untrusted content in scope and recorded
                # none did not have the escalation rule applied to it. A
                # hole in the rule, not a cosmetic difference, so it fails
                # verification.
                if in_scope.sources and not recorded:
                    gaps.append(
                        f"run {number} GATE i={index} decided "
                        f"{entry.get('action')} with {len(in_scope.sources)} "
                        "untrusted source(s) already in scope but recorded "
                        "none")
                in_scope.note(recorded, index)
                decisions.append({
                    "run": number,
                    "i": index,
                    "action": entry.get("action"),
                    "risk": entry.get("risk"),
                    "approved": bool(entry.get("approved")),
                    "reason": entry.get("reason"),
                    "tainted": recorded,
                })
            elif kind == "EXEC":
                risk = entry.get("risk")
                after = in_scope.first_at is not None
                actions.append({
                    "run": number,
                    "i": index,
                    "action": entry.get("action"),
                    "risk": risk,
                    "ok": bool(entry.get("ok")),
                    "claimed_props": list(entry.get("claimed_props") or []),
                    "artifacts": dict(entry.get("artifacts") or {}),
                    "commands": _commands(entry),
                    "after_external": after,
                    # Present only on the actions that called one, which is
                    # what makes the roster below meaningful.
                    "model": entry.get("model") or None,
                    # The worktree tree this action started from, when one
                    # was snapshotted (`ledger rewind` restores it).
                    **({"tree_before": entry["tree"]} if entry.get("tree") else {}),
                })
                if after and risk in TAINT_RISKS:
                    privileged.append({"run": number, "i": index,
                                       "action": entry.get("action"),
                                       "risk": risk})
            elif kind == "INCIDENT":
                incidents.append({
                    "run": number,
                    "i": index,
                    "type": entry.get("type"),
                    "action": entry.get("action"),
                    "claimed_not_observed":
                        list(entry.get("claimed_not_observed") or []),
                })

        summaries.append({
            "segment": segment,
            "file": segments[segment]["file"],
            "version": header.get("version"),
            "task": header.get("task_text") or header.get("task_id"),
            "goal": list(header.get("goal") or []),
            "profile": header.get("profile"),
            "branch": header.get("branch"),
            "goal_backend": header.get("goal_backend"),
            "inherited_taint": list(header.get("inherited_taint") or []),
            "first_external_at": in_scope.first_at,
            "success": bool(end.get("success")),
            "steps": end.get("steps", 0),
            "plan": list(end.get("plan") or []),
            "llm_planning_calls": end.get("llm_planning_calls", 0),
            "escalation": escalated.get("reason") if escalated else None,
        })

    last = summaries[-1] if summaries else {}
    return {
        "v": SCHEMA,
        "agent": "ledger",
        "scope": scope,
        "episode": {
            "task": summaries[0].get("task") if summaries else None,
            "branch": summaries[0].get("branch") if summaries else None,
        },
        "policy": {
            # What each profile that was actually in play means, so a
            # reader does not have to know this build's table by heart.
            "gated_risks": {p: sorted(GATED_RISKS.get(p, ()))
                            for p in sorted({s["profile"] for s in summaries
                                             if s["profile"]})},
            "taint_escalated_risks": sorted(TAINT_RISKS),
        },
        "runs": summaries,
        "decisions": decisions,
        "actions": actions,
        "incidents": incidents,
        "provenance": {
            "external_sources": seen,
            "privileged_after_external": privileged,
            # Sources the gate acted on that no journaled ingress explains.
            # Not a safety hole -- the gate was stricter than the journal
            # accounts for -- but a reader cannot see where they came from,
            # so the receipt says so rather than presenting a clean sheet.
            "unexplained_sources": [s for s in seen if s not in from_ingress],
            "gaps": gaps,
        },
        # Which models touched this episode, and what each one did. The
        # answer to "bring your own model" is only half a product without
        # it: an audit that cannot say which model wrote a line of shipped
        # code has not really said what happened.
        "models": _models(actions, summaries),
        "totals": {
            "runs": len(summaries),
            "steps": sum(s["steps"] for s in summaries),
            "llm_planning_calls": sum(s["llm_planning_calls"]
                                      for s in summaries),
            "actions": len(actions),
            "incidents": len(incidents),
            "denials": sum(1 for d in decisions if not d["approved"]),
        },
        "outcome": {
            "success": bool(last.get("success")),
            "escalation": last.get("escalation"),
        },
        "journal": {
            "chain_ok": all(s["chain_ok"] for s in segments),
            "segments": segments,
        },
    }


def _models(actions: list[dict], summaries: list[dict]) -> list[dict]:
    """Distinct models, in first-seen order, with what they were used for.

    First-seen rather than sorted so the list reads chronologically, which
    matters on a resumed episode where the model changed between attempts.
    """
    seen: dict[str, dict] = {}
    for run in summaries:
        backend = run.get("goal_backend")
        if backend:
            entry = seen.setdefault(backend, {"model": backend, "used_for": []})
            if "goal compilation" not in entry["used_for"]:
                entry["used_for"].append("goal compilation")
    for action in actions:
        model = action.get("model")
        if not model:
            continue
        entry = seen.setdefault(model, {"model": model, "used_for": []})
        name = str(action.get("action") or "")
        family = name.split(":", 1)[0]
        if family and family not in entry["used_for"]:
            entry["used_for"].append(family)
    return list(seen.values())


def extract(source: str | Path, *, scope: str = "episode") -> dict:
    """The signable body for an episode (every segment, every run) or, with
    scope="run", for the latest run of one journal file alone."""
    paths = segment_paths(source) if scope == "episode" else [Path(source)]
    return _build([(p, Journal.read(str(p))) for p in paths], scope)


def sign(body: dict, key: dict) -> dict:
    """Attach an Ed25519 signature over the canonical body."""
    body = {k: v for k, v in body.items() if k != "sig"}
    value = signing.sign(bytes.fromhex(key["seed"]),
                         canonical(body).encode("utf-8"))
    body["sig"] = {"alg": signing.ALG, "key_id": key["key_id"],
                   "public_key": key["public"], "value": value.hex()}
    return body


def issue(source: str | Path, key: dict | None = None, *,
          scope: str = "episode") -> dict:
    if key is None:
        key = signing.load_key()
    return sign(extract(source, scope=scope), key)


@dataclass
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def verify(receipt: dict, journal_path: str | Path | None = None) -> Verdict:
    """A receipt with problems is not a receipt; notes are context.

    Without a journal this answers "did the holder of this key sign these
    claims". With one it also answers "are these claims that journal",
    which is the question worth asking.
    """
    problems: list[str] = []
    notes: list[str] = []

    if receipt.get("v") != SCHEMA:
        return Verdict(False, [f"unknown receipt schema {receipt.get('v')!r} "
                               f"(this build reads {SCHEMA})"])

    sig = receipt.get("sig")
    if not isinstance(sig, dict):
        return Verdict(False, ["unsigned: no sig block"])
    if sig.get("alg") != signing.ALG:
        return Verdict(False, ["unsupported signature algorithm "
                               f"{sig.get('alg')!r}"])
    try:
        public = bytes.fromhex(str(sig.get("public_key", "")))
        value = bytes.fromhex(str(sig.get("value", "")))
    except ValueError:
        return Verdict(False, ["signature or public key is not valid hex"])

    if signing.key_id(public) != sig.get("key_id"):
        problems.append("key_id does not match the public key it travels "
                        "with")
    body = {k: v for k, v in receipt.items() if k != "sig"}
    if not signing.verify(public, canonical(body).encode("utf-8"), value):
        problems.append("signature does not verify: the receipt was altered "
                        "after signing, or signed by a different key")

    if journal_path is not None:
        problems.extend(_compare(body, journal_path, notes))

    if not receipt.get("journal", {}).get("chain_ok", False):
        problems.append("the journal this attests was already broken when "
                        "the receipt was issued")
    for gap in receipt.get("provenance", {}).get("gaps") or []:
        problems.append("provenance gap: " + gap)
    unexplained = receipt.get("provenance", {}).get("unexplained_sources")
    if unexplained:
        notes.append(f"{len(unexplained)} untrusted source(s) the gate acted "
                     "on that no journaled ingress explains: "
                     + ", ".join(str(s) for s in unexplained))

    return Verdict(not problems, problems, notes)


def _compare(body: dict, journal_path: str | Path, notes: list) -> list[str]:
    """Re-derive the body from the recorded segments and diff it.

    Deliberately from the RECORDED file list and entry counts, not from
    whatever is on disk now: an episode that was resumed after a receipt
    was issued did not make the receipt untrue, and a document that expires
    because work continued is a document nobody will rely on. Growth is
    surfaced as a note instead.
    """
    directory = Path(journal_path)
    directory = directory if directory.is_dir() else directory.parent
    recorded = body.get("journal", {}).get("segments") or []

    loaded, missing = [], []
    for segment in recorded:
        path = directory / str(segment.get("file"))
        if not path.is_file():
            missing.append(str(segment.get("file")))
            continue
        entries = Journal.read(str(path))
        counted = int(segment.get("entries") or 0)
        if len(entries) > counted:
            notes.append(f"{path.name} has grown by {len(entries) - counted} "
                         "entries since this receipt was issued")
        loaded.append((path, entries[:counted]))
    if missing:
        return [f"journal segment(s) missing from {directory}: "
                + ", ".join(missing)]

    if body.get("scope") == "episode":
        known = {str(s.get("file")) for s in recorded}
        extra = [p.name for p in segment_paths(directory)
                 if p.name not in known and p.is_file()]
        if extra:
            notes.append("the episode continued after this receipt was "
                         "issued: " + ", ".join(sorted(extra)))

    try:
        recomputed = _build(loaded, str(body.get("scope") or "episode"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"cannot rebuild the body from the journal: {exc}"]

    if canonical(recomputed) == canonical(body):
        return []
    out = [f"receipt does not match the journal at {section!r}"
           for section in sorted(set(body) | set(recomputed))
           if canonical(body.get(section)) != canonical(recomputed.get(section))]
    return out or ["receipt does not match the journal"]


def write(receipt: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def read(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
