"""What is actually being asked for? (design SS17)

A goal contract says what must be TRUE at the end -- the suite passes, the
tree is clean. It says nothing about what the thing IS. So a request one
sentence long ("a 3D chess game in three.js") reached the author as one
sentence long, and the author filled the silence with the smallest thing
that could pass: primitives on a flat board, a test API and no way to
click, no result when the game ended. Nothing was broken. Nothing had been
decided either.

This module makes the deciding a step. Before any code exists, LEDGER asks
itself the questions whose answers change what gets built, answers them,
and turns the answers into requirements: specific, checkable statements
about the finished artifact. Three properties keep that honest:

* **It can only raise the bar.** The operator's test command and any
  external acceptance check are untouched; the brief is conjoined with
  them, never substituted for them. A candidate that could lower its own
  bar would be worthless -- one that can only add to it cannot cheat by
  writing a brief, only by ignoring it.

* **It is frozen before the first patch.** The brief is written once, hashed,
  journaled, and stored in the episode directory -- outside the worktree,
  where the candidate cannot reach it. Every later attempt is judged
  against the same text, and the audit can read exactly what was demanded.

* **The verdict is a judge, so it is labelled one.** Reading source and
  deciding "this requirement is met" is opinion, not execution. It gets
  its own proposition (`brief.met`) and, like `design.ok`, is never allowed
  to stand in for `tests.pass`. What it is good for is the same thing the
  visual critique is good for: naming, specifically, what is still missing,
  so the next authoring attempt has something to build rather than a score.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .llm import extract_json

MIN_QUESTIONS, MAX_QUESTIONS = 5, 9
MAX_REQUIREMENTS = 12

QUESTION_PROMPT = """You are about to build this, and what it IS gets \
decided now -- before any code exists.

TASK
{task}

Ask the questions whose answers change what gets built. Not process \
questions, not which library: questions about the deliverable a person \
will use. What do they see in the first five seconds? What can they do, \
and how do they know they can? What happens at the edges -- the game \
ends, the input is wrong, they want to start over? What would make \
someone call this a demo rather than a finished thing?

These failures are real, and each one shipped with every test passing:
- a chess game whose pieces were untextured primitives on a flat board
- a game nothing could click, because only a test API could move a piece
- a racing game whose player started on pole, so the road ahead was empty
- a build with no way to restart and no way to see who had won
{checklist}
Ask between {lo} and {hi} questions. Each must be answerable concretely, \
and each must matter to whether the result feels finished.

Respond with ONLY a JSON object:
{{"questions": ["...", "..."]}}"""

ANSWER_PROMPT = """You asked these questions about what to build. Answer \
them now, before writing code.

TASK
{task}

QUESTIONS
{questions}

Where the task is explicit, follow it exactly. Where it is silent, choose \
what someone shipping this would choose, and say what you chose -- a \
question left open becomes a missing feature.

Then turn your answers into requirements: specific, checkable statements \
about the finished artifact. "Looks good" is not a requirement. "Every \
piece is a distinct modelled shape, not a scaled primitive" is. A \
requirement nobody could check by looking at the build is not one.

At most {max_requirements} requirements, ordered by what would be most \
embarrassing to ship without.

Respond with ONLY a JSON object:
{{"answers": [{{"question": "...", "answer": "..."}}],
  "requirements": [{{"id": "short-slug",
                     "text": "what must be true of the finished build",
                     "check": "how to tell, concretely"}}]}}"""

CHECK_PROMPT = """Here is what this build was required to be, and here is \
what it is.

REQUIREMENTS
{requirements}

FILES IN THE BUILD
{tree}

SOURCE
{source}

For each requirement, decide from the source alone: is it met or not met? \
Name the file and line that makes it met. If you cannot point at one, it \
is not met.

A plan is not an implementation. Do not accept a comment, a TODO, a \
placeholder, an empty function, or a name that merely suggests the \
feature -- only code that would do the thing when the program runs.

Respond with ONLY a JSON object:
{{"verdicts": [{{"id": "...", "met": true,
                "evidence": "path:line -- what it does"}}]}}"""


def _slug(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return cleaned[:40] or fallback


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    check: str

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "check": self.check}


@dataclass(frozen=True)
class Verdict:
    id: str
    met: bool
    evidence: str


@dataclass(frozen=True)
class Brief:
    """What the build has to be, decided once and then held still."""

    task: str
    kind: str
    questions: tuple
    answers: tuple          # ((question, answer), ...)
    requirements: tuple     # (Requirement, ...)

    def digest(self) -> str:
        """Content hash of the bar. Journaled, so an audit can prove the
        requirements were not edited once the work got hard."""
        blob = json.dumps(
            {"task": self.task, "kind": self.kind,
             "requirements": [r.to_dict() for r in self.requirements]},
            sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {"task": self.task, "kind": self.kind,
                "questions": list(self.questions),
                "answers": [{"question": q, "answer": a}
                            for q, a in self.answers],
                "requirements": [r.to_dict() for r in self.requirements],
                "digest": self.digest()}

    @classmethod
    def from_dict(cls, raw: dict) -> "Brief | None":
        try:
            requirements = tuple(
                Requirement(str(r["id"]), str(r["text"]), str(r.get("check", "")))
                for r in raw.get("requirements") or []
                if str(r.get("text") or "").strip())
            if not requirements:
                return None
            return cls(
                task=str(raw.get("task") or ""),
                kind=str(raw.get("kind") or ""),
                questions=tuple(str(q) for q in raw.get("questions") or []),
                answers=tuple(
                    (str(a.get("question") or ""), str(a.get("answer") or ""))
                    for a in raw.get("answers") or []),
                requirements=requirements)
        except (AttributeError, KeyError, TypeError):
            return None

    def as_prompt(self) -> str:
        """What goes into every authoring attempt."""
        lines = ["WHAT THIS BUILD MUST BE (decided before any code, and "
                 "fixed since)",
                 "A build that leaves one of these open is unfinished, "
                 "whatever its tests say:"]
        for requirement in self.requirements:
            lines.append(f"- {requirement.id}: {requirement.text}")
            if requirement.check:
                lines.append(f"    how to tell: {requirement.check}")
        return "\n".join(lines)


def unmet_block(verdicts: list) -> str:
    """The judge's findings, as evidence for the next attempt."""
    unmet = [v for v in verdicts if not v.met]
    if not unmet:
        return ""
    lines = ["CHECKED AGAINST THE BRIEF -- these are NOT met yet:"]
    for index, verdict in enumerate(unmet, 1):
        lines.append(f"  {index}. {verdict.id}: {verdict.evidence}")
    lines.append("Build these. They are what the deliverable is missing.")
    return "\n".join(lines)


def _json_call(backend, prompt: str) -> dict:
    """A brief must never take an episode with it: any failure is an empty
    answer, and the caller degrades to no brief at all."""
    try:
        obj = extract_json(backend.complete(prompt))
    except Exception:                                   # noqa: BLE001
        return {}
    return obj if isinstance(obj, dict) else {}


def ask(task: str, kind: str, backend) -> list[str]:
    """The questions. Bounded, and about the deliverable."""
    from .product import requirements as kind_requirements
    items = kind_requirements(kind)
    checklist = ""
    if items:
        checklist = ("\nFor this kind of thing these always matter, so ask "
                     "past them rather than about them:\n"
                     + "\n".join(f"- {d}" for d, _ in items) + "\n")
    obj = _json_call(backend, QUESTION_PROMPT.format(
        task=task, checklist=checklist, lo=MIN_QUESTIONS, hi=MAX_QUESTIONS))
    questions = [str(q).strip() for q in obj.get("questions") or []
                 if str(q).strip()]
    return questions[:MAX_QUESTIONS]


def answer(task: str, questions: list[str], backend) -> tuple[list, list]:
    """Answers, and the requirements they imply."""
    if not questions:
        return [], []
    obj = _json_call(backend, ANSWER_PROMPT.format(
        task=task, questions="\n".join(f"{i}. {q}" for i, q
                                       in enumerate(questions, 1)),
        max_requirements=MAX_REQUIREMENTS))
    answers = [(str(a.get("question") or ""), str(a.get("answer") or ""))
               for a in obj.get("answers") or [] if isinstance(a, dict)]
    requirements, seen = [], set()
    for index, raw in enumerate(obj.get("requirements") or [], 1):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        identifier = _slug(raw.get("id") or text, f"req-{index}")
        if identifier in seen:
            identifier = f"{identifier}-{index}"
        seen.add(identifier)
        requirements.append(Requirement(
            id=identifier, text=text[:400],
            check=str(raw.get("check") or "").strip()[:300]))
        if len(requirements) >= MAX_REQUIREMENTS:
            break
    return answers, requirements


def write_brief(task: str, kind: str, backend, destination: Path):
    """Interrogate the request, then freeze the answer. None if the model
    could not produce one -- an episode without a brief is the old
    behaviour, not a failure."""
    destination = Path(destination)
    questions = ask(task, kind, backend)
    answers, requirements = answer(task, questions, backend)
    if not requirements:
        return None
    brief = Brief(task=task, kind=kind, questions=tuple(questions),
                  answers=tuple(answers), requirements=tuple(requirements))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(brief.to_dict(), indent=2) + "\n",
                           encoding="utf-8")
    return brief


def load_brief(path: Path):
    """Read a frozen brief. Never rewrites it: a resumed episode is held to
    what it was held to before."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Brief.from_dict(raw) if isinstance(raw, dict) else None


_SOURCE_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".py",
                    ".html", ".css", ".gd", ".vue", ".svelte", ".rs", ".go",
                    ".java", ".rb", ".c", ".cpp", ".cs")
_SKIP_PARTS = ("node_modules/", "dist/", "build/", "vendor/", "coverage/",
               "package-lock.json")


def source_files(brief: Brief, worktree: Path, k: int = 6) -> list[str]:
    """Which files the judge reads. Deterministic, and never empty while the
    repository holds source.

    Ranking by overlap with the request alone was not enough: a brief that
    says "every piece is a distinct shape" shares no vocabulary with a file
    called `scene.js`, so the judge was handed nothing, reported "not
    assessed" every time, and `brief.met` could never become true however
    good the build got. Relevance first, then simply the source.
    """
    from .skills_llm import repo_tree, select_context
    query = brief.task + "\n" + "\n".join(r.text for r in brief.requirements)
    chosen = list(select_context(worktree, query, k=k))
    if len(chosen) >= k:
        return chosen
    tracked = [line for line in repo_tree(Path(worktree)).splitlines()
               if line and not line.startswith("...")]

    def rank(rel: str) -> tuple:
        low = rel.replace("\\", "/").lower()
        entry = any(mark in low for mark in
                    ("index.", "main.", "app.", "src/", "game."))
        return (0 if entry else 1, low.count("/"), low)

    for rel in sorted((r for r in tracked
                       if r.lower().endswith(_SOURCE_SUFFIXES)
                       and not any(skip in r.replace("\\", "/").lower()
                                   for skip in _SKIP_PARTS)), key=rank):
        if rel not in chosen:
            chosen.append(rel)
        if len(chosen) >= k:
            break
    return chosen


def check(brief: Brief, worktree: Path, backend, max_chars: int = 30000):
    """Judge the build against the frozen requirements.

    None when there was nothing to judge or the model could not answer --
    distinct from "judged and found wanting", which is a list of verdicts.
    """
    from .skills_llm import read_files, repo_tree
    worktree = Path(worktree)
    files = source_files(brief, worktree)
    source = read_files(worktree, files, max_bytes=max_chars) if files else ""
    if not source.strip():
        return None
    obj = _json_call(backend, CHECK_PROMPT.format(
        requirements="\n".join(
            f"- {r.id}: {r.text}" + (f" (how to tell: {r.check})" if r.check
                                     else "")
            for r in brief.requirements),
        tree=repo_tree(worktree), source=source))
    raw_verdicts = obj.get("verdicts")
    if not isinstance(raw_verdicts, list):
        return None
    known = {r.id: r for r in brief.requirements}
    seen, verdicts = set(), []
    for raw in raw_verdicts:
        if not isinstance(raw, dict):
            continue
        identifier = str(raw.get("id") or "").strip()
        if identifier not in known or identifier in seen:
            continue                        # not on the frozen list; not a verdict
        seen.add(identifier)
        verdicts.append(Verdict(
            id=identifier, met=bool(raw.get("met")),
            evidence=str(raw.get("evidence") or "")[:300]))
    # Silence is not assent. A requirement the judge skipped is unmet, so a
    # short reply cannot pass a build by omission.
    for identifier, requirement in known.items():
        if identifier not in seen:
            verdicts.append(Verdict(
                id=identifier, met=False,
                evidence="not addressed by the review: " + requirement.text))
    return verdicts
