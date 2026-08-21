"""Is this a thing a person can actually use? (design SS15)

A goal contract is a set of sensor propositions, and a suite of tests can be
entirely green while the thing nobody can operate. Every product failure in
this project's own history was that shape, not a bug:

* a chess game with no opponent -- the rules worked perfectly, and you had
  to move for both sides
* a 3D scene whose file mentioned `Renderer` while the screen stayed black
* a racing game whose steering input was stored on the car and read by
  nothing, so the car drove in a straight line for the whole race; the
  throttle test passed throughout, because the throttle worked
* a race where the player started on pole, so the entire field was behind
  the camera and the road ahead was empty

None of those are subtle. All of them survived a green suite, because the
contract described MECHANICS and nobody had written down that the point of
a game is that somebody plays it.

So this module holds what "usable" means for a kind of software, in two
places that matter:

1. It goes INTO the authoring prompt, so the thing gets built with controls
   and feedback in the first place.
2. It is reviewed against the task before the work starts, so a contract
   that cannot describe a working product is flagged while it is still
   cheap to change.

Deliberately not a gate by default. A checklist that blocks a build is a
checklist people turn off; one that tells them what they are about to miss
is one they keep.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .llm import extract_json

#: kind -> (label, [(dimension, requirement)])
CHECKLISTS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "game": ("a game", [
        ("controls", "Every input the player is told about must reach the "
                     "simulation and visibly change it. Storing an input on "
                     "an object that nothing reads is the single most common "
                     "way a game ships unplayable."),
        ("challenge", "Something must oppose the player: an opponent that "
                      "acts on its own, a hazard, a clock. A game you cannot "
                      "lose is a screensaver, and one where you play both "
                      "sides is a board, not a game."),
        ("feedback", "The player must be able to see their own state -- "
                     "score, position, health, progress -- without reading "
                     "the console."),
        ("outcome", "There must be a way to win and a way to lose, both "
                    "reachable, and the game must say which happened."),
        ("first_5_seconds", "Something must be moving and visible when the "
                            "screen first appears. A correct scene that "
                            "renders nothing is indistinguishable from a "
                            "crash."),
    ]),
    "web": ("a web interface", [
        ("input", "Every control must be wired to the state it claims to "
                  "change, and the change must be visible."),
        ("feedback", "Loading, empty and error states must each show "
                     "something. A blank panel is not an empty state."),
        ("errors", "A failed request must say what failed and what to do, "
                   "not fail silently or spin forever."),
        ("first_paint", "The page must render meaningful content, not a "
                        "blank body with a console error."),
        ("keyboard", "Interactive elements must be reachable and operable "
                     "from the keyboard."),
    ]),
    "cli": ("a command-line tool", [
        ("help", "Running it with no arguments, or --help, must explain "
                 "what it does and show the next command to type -- not an "
                 "argument-parser error."),
        ("errors", "Every failure must name what went wrong AND what to do "
                   "about it. 'not found' is a dead end."),
        ("exit_codes", "Success exits 0 and failure does not, so it can be "
                       "used in a script."),
        ("output", "Output must be readable when piped or redirected, not "
                   "only in a terminal."),
    ]),
    "service": ("a service or API", [
        ("contract", "Every endpoint must state its inputs, outputs and "
                     "error shapes."),
        ("errors", "Failures must return a useful status and a message a "
                   "caller can act on."),
        ("health", "There must be a way to ask whether it is working."),
        ("limits", "Behaviour under bad input, missing fields and oversized "
                   "payloads must be defined."),
    ]),
    "library": ("a library", [
        ("api", "The public surface must be small, named consistently, and "
                "usable without reading the implementation."),
        ("errors", "Failures must raise something catchable and specific."),
        ("examples", "There must be a runnable example of the main use."),
    ]),
}

_HINTS = (
    # Wider than the obvious words. "roll the ball down a platform and avoid
    # falling through the holes" is unmistakably a game and contains none of
    # game/player/level/score -- it was missed, which is exactly the request
    # that most needed the gameplay checklist.
    ("game", re.compile(
        r"\b(game|gameplay|player|level|enem(y|ies)|score|physics|"
        r"three[\s.]?js|unity|godot|sprite|render loop|playable|arcade|"
        r"puzzle|platformer|ball|jump(ing)?|dodge|obstacle|hazard|"
        r"respawn|power-?up|lives|boss|collision|joystick|gamepad|"
        r"racing|race car|first-person|third-person)\b", re.I)),
    ("web", re.compile(
        r"\b(web ?app|website|page|react|vue|svelte|frontend|ui|button|"
        r"form|dashboard|html|css)\b", re.I)),
    ("cli", re.compile(
        r"\b(cli|command[- ]line|terminal|argparse|subcommand|flag)\b", re.I)),
    ("service", re.compile(
        r"\b(api|endpoint|server|service|rest|graphql|http handler|"
        r"microservice)\b", re.I)),
    ("library", re.compile(
        r"\b(library|npm package|pypi|sdk|public api)\b", re.I)),
)


def detect_kind(task_text: str, default: str = "") -> str:
    """Guess what is being built, from the task. First match wins, in the
    order above -- 'a game with a web UI' is a game, because the gameplay
    requirements are the ones nobody writes down."""
    text = task_text or ""
    for kind, pattern in _HINTS:
        if pattern.search(text):
            return kind
    return default


def requirements(kind: str, only: list[str] | None = None) -> list[tuple[str, str]]:
    entry = CHECKLISTS.get(kind)
    if not entry:
        return []
    items = entry[1]
    if only:
        wanted = {d.strip().lower() for d in only}
        items = [i for i in items if i[0] in wanted]
    return items


def prompt_block(kind: str, only: list[str] | None = None) -> str:
    """The part that goes into the authoring prompt.

    Cheaper than any review: the requirements are in front of the model
    while it writes, rather than being discovered afterwards by a person
    who tried to play the thing.
    """
    items = requirements(kind, only)
    if not items:
        return ""
    label = CHECKLISTS[kind][0]
    lines = [f"WHAT MAKES {label.upper()} ACTUALLY USABLE",
             "These are not optional polish. A build that satisfies its "
             "tests but fails these is not finished:"]
    for dimension, text in items:
        lines.append(f"- {dimension}: {text}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Concern:
    dimension: str
    concern: str
    check: str

    def line(self) -> str:
        return f"{self.dimension}: {self.concern}"


REVIEW_PROMPT = """You are reviewing a build request before any work starts.

WHAT WAS ASKED FOR
{task}

HOW SUCCESS WILL BE MEASURED
{contract}

For {label}, these are the things that make it actually usable:
{checklist}

Which of them would NOT be guaranteed by the request and its success \
measure as they stand? For each gap, say what could ship broken and what \
check would catch it.

Judge only what is written. Do not invent requirements outside the list \
above, and do not raise a gap that the success measure already covers.

Respond with ONLY a JSON object:
{{"gaps": [{{"dimension": "controls",
            "concern": "<what could ship broken, one sentence>",
            "check": "<a check that would catch it>"}}]}}
If nothing is missing, return {{"gaps": []}}."""


def review(task_text: str, contract_text: str, kind: str, backend,
           only: list[str] | None = None) -> list[Concern]:
    """Gaps between what was asked for and what makes it usable.

    Never raises: a review that fails must not take the episode with it.
    Its output is advice, and advice that can break a build is worse than
    no advice.
    """
    items = requirements(kind, only)
    if not items or backend is None:
        return []
    prompt = REVIEW_PROMPT.format(
        task=task_text or "(none given)",
        contract=contract_text or "(none given)",
        label=CHECKLISTS[kind][0],
        checklist="\n".join(f"- {d}: {t}" for d, t in items))
    try:
        obj = extract_json(backend.complete(prompt))
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []
    known = {d for d, _ in items}
    out = []
    for raw in obj.get("gaps") or []:
        if not isinstance(raw, dict):
            continue
        dimension = str(raw.get("dimension") or "").strip().lower()
        if dimension not in known:
            continue                  # not on the list; not a finding
        out.append(Concern(dimension=dimension,
                           concern=str(raw.get("concern") or "")[:300],
                           check=str(raw.get("check") or "")[:300]))
    return out
