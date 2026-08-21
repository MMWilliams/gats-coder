"""Proposing actions the catalog does not have (design SS14).

LEDGER's catalog is fixed: run the suite, author a patch, apply it, commit,
push, open a PR. When a task needs anything else -- a dependency installed,
a migration run, an asset generated, a tool invoked -- no action's effects
reach the goal, the planner explores, finds nothing, and escalates
`no_path`. That is the wall between "I was told exactly how" and "work out
how".

This module proposes new actions to get over it, under the same rule that
governs everything else here: **an effect is what a sensor confirms, never
what the model claims.** A proposal is a hypothesis with a command attached.
The planner still chooses deterministically by UCB1; the sensors still
decide what actually happened; a proposal whose claimed effect does not
materialise diverges, is recorded to L2, and stops being selected.

So the model is being asked what is POSSIBLE, not what is true. Nothing it
says here is taken on trust:

* Every proposition it names must already exist in the sensor vocabulary.
  It cannot invent `deps.installed` and have that mean anything -- if no
  instrument reports it, the goal can never be shown to be met through it.
* Its effects must intersect what is actually missing, or the proposal is
  noise dressed as progress.
* Its command runs behind the same PolicyGate as everything else, at the
  risk class it declares, and a wrong declaration is caught by the gate
  rather than by the user afterwards.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .actions import Action
from .llm import extract_json

RISKS = ("read", "write", "exec", "network")

# `curl https://host/install.sh | sh` and friends -- a fetch on one side of a
# pipe and an interpreter on the other, whatever sits between them.
_PIPE_TO_SHELL = re.compile(
    r"\b(curl|wget|iwr|invoke-webrequest)\b[^|]*\|\s*"
    r"(sudo\s+)?(sh|bash|zsh|dash|python\d?|perl|ruby|node|iex)\b")

PROMPT = """You are the strategist for a planning agent working in a git \
repository. The agent plans over a fixed catalogue of actions and has run \
out of moves: nothing it can do reaches the goal.

TASK
{task}

WHAT IS TRUE RIGHT NOW
{state}

WHAT THE GOAL STILL NEEDS
{unmet}

ACTIONS IT ALREADY HAS
{catalog}

The agent's instruments can only observe these propositions. This is the \
complete list; anything outside it is invisible to the agent and cannot be \
used in a precondition or an effect:
{vocab}
{research}
Propose up to {limit} concrete actions that would move the agent towards \
what the goal still needs. For each one give a shell command that would \
actually accomplish it on this machine.

Respond with ONLY a JSON object:
{{"actions": [
  {{"name": "InstallDeps",
    "command": "npm install",
    "prec": [],
    "add": ["<proposition from the list above>"],
    "delete": [],
    "risk": "exec",
    "why": "<one sentence>"}}
]}}

Rules:
- Every string in "prec", "add" and "delete" MUST appear verbatim in the \
list of observable propositions. An action claiming anything else is \
discarded.
- "add" must include at least one of the propositions the goal still needs.
- "risk" is one of: read, write, exec, network.
- "command" runs from the repository root. It must be non-interactive.
- Prefer the smallest command that does the job. No pipes into shells, no \
`sudo`, nothing that rewrites history."""


@dataclass(frozen=True)
class Proposal:
    action: Action
    command: str
    risk: str
    why: str


def _clean(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9:_.-]", "", str(name))[:48]


def validate(raw: dict, vocabulary: set[str], unmet: set[str],
             existing: set[str]) -> tuple[Proposal | None, str]:
    """One proposal, checked. Returns (proposal, reason-if-rejected)."""
    name = _clean(raw.get("name") or "")
    if not name:
        return None, "no name"
    if name in existing:
        return None, f"{name} already exists"

    command = str(raw.get("command") or "").strip()
    if not command:
        return None, f"{name} has no command"
    # Refuse the shapes that are never the smallest way to do something and
    # are how an agent quietly destroys a working tree.
    lowered = command.lower()
    for banned in ("sudo ", "rm -rf /", "git push --force", "git reset --hard",
                   "git clean -", "mkfs", "shutdown", "chmod 777"):
        if banned in lowered:
            return None, f"{name} runs a refused command shape: {banned!r}"
    # Piping a download into an interpreter, matched as the PATTERN it is.
    # A literal "curl | sh" catches nothing: the real shape is
    # `curl https://host/install.sh | sh`, with the URL in between.
    if _PIPE_TO_SHELL.search(lowered):
        return None, (f"{name} pipes a download straight into a shell, which "
                      "runs code nobody has seen")

    risk = str(raw.get("risk") or "exec").lower()
    if risk not in RISKS:
        return None, f"{name} has unknown risk {risk!r}"

    def props(key):
        value = raw.get(key) or []
        return frozenset(str(p) for p in value) if isinstance(value, list) \
            else frozenset()

    prec, add, delete = props("prec"), props("add"), props("delete")
    invented = sorted((prec | add | delete) - vocabulary)
    if invented:
        # The heart of it: an effect no instrument can see is not an effect.
        return None, (f"{name} names propositions no sensor reports: "
                      f"{invented}")
    if not add:
        return None, f"{name} claims no effect"
    if not (add & unmet):
        return None, (f"{name} does not advance anything the goal still "
                      "needs")

    action = Action(name=name, prec=prec, add=add, delete=delete,
                    deterministic=False, effects_known=False)
    return Proposal(action=action, command=command, risk=risk,
                    why=str(raw.get("why") or "")[:200]), ""


def propose(task_text: str, state, unmet, catalog, vocabulary, backend,
            research_text: str = "", limit: int = 4,
            attempts: int = 2) -> tuple[list[Proposal], list[str]]:
    """Ask for candidate actions; return the ones that survive checking.

    Returns (proposals, notes) -- notes records every rejection with its
    reason, so the journal shows what was suggested AND why it was refused.
    A strategist whose failures are invisible cannot be debugged.
    """
    existing = {a.name for a in catalog}
    vocab = set(vocabulary)
    unmet = set(unmet)
    notes: list[str] = []

    block = ""
    if research_text:
        block = ("\nCURRENT DOCUMENTATION (retrieved just now -- prefer this "
                 "over what you remember, it may have changed)\n"
                 + research_text[:12000] + "\n")

    prompt = PROMPT.format(
        task=task_text or "(no task text)",
        state="\n".join(f"- {p}" for p in sorted(state)) or "- (nothing)",
        unmet="\n".join(f"- {p}" for p in sorted(unmet)) or "- (nothing)",
        catalog="\n".join(f"- {a.name}" for a in catalog) or "- (none)",
        vocab="\n".join(f"- {p}" for p in sorted(vocab)),
        research=block,
        limit=limit)

    for attempt in range(max(1, attempts)):
        try:
            reply = backend.complete(prompt)
        except Exception as exc:
            notes.append(f"strategist backend failed: {exc}")
            return [], notes
        obj = extract_json(reply)
        if obj is None:
            notes.append("strategist reply was not JSON")
            continue
        raw_actions = obj.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            notes.append("strategist proposed nothing")
            continue

        kept: list[Proposal] = []
        for raw in raw_actions[:limit]:
            if not isinstance(raw, dict):
                continue
            proposal, why_not = validate(raw, vocab, unmet,
                                         existing | {p.action.name
                                                     for p in kept})
            if proposal is None:
                notes.append(f"rejected: {why_not}")
                continue
            kept.append(proposal)
            notes.append(f"accepted: {proposal.action.name} "
                         f"({proposal.risk}) -- {proposal.why}")
        if kept:
            return kept, notes
    return [], notes


def research_query(task_text: str, unmet) -> str:
    """What to look up. Deliberately built from the unmet propositions
    rather than the whole task: the task is prose, and the propositions are
    the part that is actually stuck."""
    missing = " ".join(sorted(unmet))[:120]
    head = (task_text or "").strip().splitlines()[0][:120] if task_text else ""
    return f"{head} {missing}".strip() or "software build troubleshooting"
