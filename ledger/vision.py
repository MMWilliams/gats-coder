"""Looking at what was built (design SS16).

Every sensor LEDGER had reported something a program can decide: the suite
passed, the tree is clean, the branch exists. So every goal it could pursue
was of that kind, and a build came out correct and ugly -- because nothing
in the loop had ever seen it.

This is the missing instrument. It drives a headless browser, screenshots
the running thing, and asks a vision model to score it against an explicit
rubric. Two rules make that safe to put in a control loop:

* **It is a judge, not a verifier.** Execution rewards are checkable and
  hard to game; a model's opinion is neither. So its verdict is reported
  as its own proposition (`design.ok`) and never allowed to stand in for
  `tests.pass`. Something that looks superb and fails its tests has not
  passed. The rubric is fixed and explicit precisely because an unrubric'd
  judge drifts and can be flattered.
* **Its complaints are worth more than its score.** The score gates; the
  written critique goes back into the authoring prompt as evidence, the
  same way a failing test's output does. "The horizon is a hard line with
  no atmosphere" is actionable in a way that "6/10" is not.

Local by default (Ollama, so nothing leaves the machine) and entirely
optional -- absent a vision model this degrades to "not assessed" rather
than blocking anything.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "qwen3-vl:32b"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

# The rubric is part of the contract, not a mood. Each dimension is scored
# 0-10 with a stated meaning, so two runs are comparable and the model has
# something to argue against rather than a vibe to match.
RUBRIC = [
    ("composition", "Framing and layout. Is the subject readable, is the "
                    "camera placed so you can see what matters, is the "
                    "screen balanced rather than empty or cluttered?"),
    ("lighting", "Light and shade. Is there directional light, contact "
                 "shadow, and tonal range -- or is everything flatly lit "
                 "with no sense of depth?"),
    ("materials", "Surfaces. Do things read as made of something, with "
                  "texture and varied colour, or as untextured primitives?"),
    ("interface", "The HUD and text. Is it legible, aligned, and quiet "
                  "enough to ignore, or is it default-styled and noisy?"),
    ("finish", "Overall polish. Would a person believe this was made "
               "deliberately, or does it look like a placeholder?"),
]

PROMPT = """You are reviewing a screenshot of a running application, as a \
demanding art director would. Be specific and be harsh: vague praise is \
useless to the person who has to fix it.

Score each dimension from 0 to 10.
{rubric}

Then list the THREE most damaging specific problems, each phrased as an \
instruction that could be acted on. Not "improve the lighting" but "the \
scene has a single ambient light so every surface is the same brightness; \
add a directional key light and enable shadow casting".

Respond with ONLY a JSON object:
{{"scores": {{"composition": 0, "lighting": 0, "materials": 0, \
"interface": 0, "finish": 0}},
  "problems": ["...", "...", "..."],
  "summary": "<one sentence>"}}"""


@dataclass
class Critique:
    scores: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    summary: str = ""
    ok: bool = False
    assessed: bool = False
    error: str = ""

    @property
    def mean(self) -> float:
        if not self.scores:
            return 0.0
        return sum(self.scores.values()) / len(self.scores)

    def as_evidence(self) -> str:
        """What goes back into the authoring prompt."""
        if not self.assessed:
            return ""
        lines = [f"VISUAL REVIEW OF THE RUNNING BUILD (mean {self.mean:.1f}/10)"]
        for name, score in sorted(self.scores.items()):
            lines.append(f"- {name}: {score}/10")
        if self.summary:
            lines.append(f"summary: {self.summary}")
        if self.problems:
            lines.append("The three most damaging problems, to be fixed:")
            for i, problem in enumerate(self.problems, 1):
                lines.append(f"  {i}. {problem}")
        return "\n".join(lines)


def _chrome() -> str | None:
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        return found
    for candidate in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/chromium"):
        if Path(candidate).exists():
            return candidate
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def screenshot(root: Path | str, out: Path, *, wait_ms: int = 9000,
               size: str = "1280,800", page: str = "index.html") -> bool:
    """Serve `root` and photograph it. True if an image was produced.

    Served over http, never opened as file:// -- ES modules are blocked on
    that origin, so a module-based app photographs as a blank page and the
    critique would be of nothing.
    """
    chrome = _chrome()
    if chrome is None:
        return False
    root = Path(root)
    port = _free_port()
    server = subprocess.Popen(
        ["python", "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-first-run", "--hide-scrollbars",
             "--user-data-dir=" + tempfile.mkdtemp(prefix="ledger-shot-"),
             f"--virtual-time-budget={wait_ms}",
             f"--screenshot={out}", f"--window-size={size}",
             f"http://127.0.0.1:{port}/{page}"],
            capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    return out.is_file() and out.stat().st_size > 1000


def critique_image(path: Path, model: str = DEFAULT_MODEL,
                   threshold: float = 6.5, timeout: int = 600) -> Critique:
    """Ask a local vision model to score one screenshot."""
    if not Path(path).is_file():
        return Critique(error="no screenshot")
    rubric = "\n".join(f"- {n}: {d}" for n, d in RUBRIC)
    payload = {
        "model": model,
        "prompt": PROMPT.format(rubric=rubric),
        "images": [base64.b64encode(Path(path).read_bytes()).decode("ascii")],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError) as exc:
        return Critique(error=f"vision model unreachable: {exc}")

    from .llm import extract_json
    obj = extract_json(body.get("response", "") or "")
    if not isinstance(obj, dict):
        return Critique(error="the vision model did not return JSON")

    raw = obj.get("scores") or {}
    scores = {}
    for name, _ in RUBRIC:
        try:
            scores[name] = max(0.0, min(10.0, float(raw.get(name, 0))))
        except (TypeError, ValueError):
            scores[name] = 0.0
    problems = [str(p)[:400] for p in (obj.get("problems") or [])][:3]
    c = Critique(scores=scores, problems=problems,
                 summary=str(obj.get("summary") or "")[:300], assessed=True)
    c.ok = c.mean >= threshold
    return c


def review(root: Path | str, *, model: str = DEFAULT_MODEL,
           threshold: float = 6.5, page: str = "index.html") -> Critique:
    """Screenshot a built app and critique it. Never raises."""
    shot = Path(tempfile.mkdtemp(prefix="ledger-vision-")) / "shot.png"
    if not screenshot(root, shot, page=page):
        return Critique(error="could not photograph the build "
                              "(no headless Chrome, or no index.html)")
    return critique_image(shot, model=model, threshold=threshold)
