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
import struct
import subprocess
import sys
import tempfile
import time
import zlib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# gemma4:31b, chosen by measurement rather than reputation. Asked the same
# pair in both orders, qwen3-vl:32b and qwen2.5vl:7b answered "the second
# one" every time -- including when both images were IDENTICAL -- so their
# judgments carried no information at all. gemma4 ranked a known pair 3/3
# correctly in both orderings and correctly called identical images the same.
# Its ABSOLUTE scores are still worthless (it rates the better build 2.0 and
# the worse one 2.2), which is why the gate below is pairwise.
DEFAULT_MODEL = "gemma4:31b"
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
    # A machine with Playwright has a Chromium already, in its own cache.
    for cache in (Path.home() / "AppData" / "Local" / "ms-playwright",
                  Path.home() / ".cache" / "ms-playwright"):
        for candidate in sorted(cache.glob("chromium-*/chrome-*/chrome*"),
                                reverse=True):
            if candidate.is_file() and candidate.suffix in ("", ".exe"):
                return str(candidate)
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def page_root(root: Path | str) -> Path | None:
    """The directory whose index.html is the thing to photograph.

    A bundler project's root index.html imports bare module names that no
    plain server can resolve, so served as-is it photographs as a blank
    page with a console error -- indistinguishable from a build that draws
    nothing. The built output is what a person would open; prefer it.
    """
    root = Path(root)
    for sub in ("dist", "build"):
        if (root / sub / "index.html").is_file():
            return root / sub
    if (root / "index.html").is_file():
        return root
    return None


def ensure_built(root: Path | str, timeout: int = 300) -> bool:
    """Refresh a bundler's output when the source is newer than it.

    True when there is nothing to build or the build succeeded. The suite
    usually builds already; this covers the order the planner may choose,
    where a photograph is taken of an output older than the patch.
    """
    root = Path(root)
    package = root / "package.json"
    if not package.is_file() or shutil.which("npm") is None:
        return True
    try:
        scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
    except (ValueError, OSError):
        return True
    if "build" not in scripts:
        return True
    output = root / "dist" / "index.html"
    newest_source = 0.0
    for sub in ("src", "index.html", "package.json"):
        path = root / sub
        candidates = path.rglob("*") if path.is_dir() else [path]
        for item in candidates:
            try:
                if item.is_file():
                    newest_source = max(newest_source, item.stat().st_mtime)
            except OSError:
                pass
    if output.is_file() and output.stat().st_mtime >= newest_source:
        return True
    try:
        # npm on Windows is a .cmd shim: only a shell can start it.
        done = subprocess.run("npm run build", cwd=str(root), shell=True,
                              capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _unfilter(kind: int, raw: bytes, prior: bytes, bpp: int) -> bytearray:
    line = bytearray(raw)
    if kind == 0:
        return line
    if kind == 1:
        for i in range(bpp, len(line)):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
    elif kind == 2:
        for i in range(len(line)):
            line[i] = (line[i] + prior[i]) & 0xFF
    elif kind == 3:
        for i in range(len(line)):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + prior[i]) >> 1)) & 0xFF
    elif kind == 4:
        for i in range(len(line)):
            a = line[i - bpp] if i >= bpp else 0
            b = prior[i]
            c = prior[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            line[i] = (line[i] + predictor) & 0xFF
    return line


def png_stats(path: Path | str, samples: int = 20000) -> dict | None:
    """Colour count and contrast of a PNG, decoded with the stdlib.

    Enough to tell a painted screen from a flat one, which is the one
    question the usability probe asks of a screenshot. Handles the 8-bit,
    non-interlaced grey/RGB/RGBA files headless Chrome writes; anything
    else is reported as None (not assessed), never as blank.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    offset, width = 8, 0
    height = depth = colour = interlace = 0
    idat = bytearray()
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        chunk = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
        offset += 12 + length
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour)
    if not (width and height and channels) or depth != 8 or interlace:
        return None
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return None
    stride = width * channels
    if len(raw) < height * (stride + 1):
        return None
    prior = bytearray(stride)
    step = max(1, (width * height) // samples)
    luma_sum = luma_sq = 0.0
    count = 0
    colours: set = set()
    position = 0
    for row in range(height):
        start = row * (stride + 1)
        line = _unfilter(raw[start], raw[start + 1:start + 1 + stride],
                         prior, channels)
        for x in range(width):
            if position % step == 0:
                base = x * channels
                if channels >= 3:
                    r, g, b = line[base], line[base + 1], line[base + 2]
                else:
                    r = g = b = line[base]
                luma = 0.299 * r + 0.587 * g + 0.114 * b
                luma_sum += luma
                luma_sq += luma * luma
                count += 1
                colours.add((r >> 4, g >> 4, b >> 4))
            position += 1
        prior = line
    if not count:
        return None
    mean = luma_sum / count
    variance = max(0.0, luma_sq / count - mean * mean)
    return {"width": width, "height": height, "distinct": len(colours),
            "mean": mean, "stddev": variance ** 0.5}


def looks_blank(stats: dict | None) -> bool:
    """A screen with a couple of colours and no contrast is not a first
    paint; it is the background. None means not assessed, and not
    assessed is not blank."""
    if not stats:
        return False
    return stats["distinct"] < 12 or stats["stddev"] < 6.0


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
    if not (root / page).is_file():
        return False
    port = _free_port()
    # sys.executable, not "python": an episode does not necessarily run with
    # a `python` on PATH, and a server that never started photographs as a
    # connection-refused page -- which is indistinguishable from an ugly one.
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Wait for the port to ACCEPT, rather than sleeping a fixed interval.
        # 1.5s was enough on an idle machine and not enough while the episode
        # was running its own test suite, so the review failed intermittently
        # for reasons that had nothing to do with the build.
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                if server.poll() is not None:
                    return False          # the server died; nothing to shoot
                time.sleep(0.2)
        else:
            return False
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


COMPARE_PROMPT = """Two screenshots of the same application, before and \
after a change. The FIRST image is labelled A, the SECOND is B.

Which is better made? Judge only what is visible: lighting and shade, \
surface texture and material variety, framing, and whether the interface is \
legible and deliberate.

Respond with ONLY a JSON object:
{{"better": "A" | "B" | "same", "why": "<one sentence naming what differs>"}}"""


@dataclass
class Comparison:
    better: str = "same"          # "before" | "after" | "same"
    why: str = ""
    agreed: bool = False          # both orderings gave the same answer
    assessed: bool = False
    error: str = ""


def _ask_pair(first: Path, second: Path, model: str, timeout: int) -> str:
    """One pairwise judgment. Returns 'A', 'B' or 'same'."""
    payload = {
        "model": model,
        "prompt": COMPARE_PROMPT,
        "images": [base64.b64encode(Path(p).read_bytes()).decode("ascii")
                   for p in (first, second)],
        "stream": False,
        "options": {"temperature": 0.0},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
    from .llm import extract_json
    obj = extract_json(body.get("response", "") or "")
    if not isinstance(obj, dict):
        return "same"
    verdict = str(obj.get("better", "same")).strip().upper()
    return verdict if verdict in ("A", "B") else "same"


def compare(before: Path, after: Path, *, model: str = DEFAULT_MODEL,
            timeout: int = 600) -> Comparison:
    """Which of two builds looks better -- asked BOTH ways round.

    Absolute scoring from this judge turned out to be unusable: measured on
    a pair whose difference was night and day, it awarded 2.7 and 3.2, and
    justified the low mark by asserting there was no directional light in a
    scene containing two of them and thirteen shadow references. The
    compressed scale made any fixed threshold either unreachable or
    meaningless.

    Ranking, though, it does reliably -- it got the same pair right three
    times out of three. So the question asked here is the one it can answer.

    Asked in both orders because position bias is the standard failure of a
    pairwise judge: a model that simply prefers the second image would look
    perfect on a test where the improved version is always second. If the
    two orderings disagree, the honest answer is that they are
    indistinguishable, not that one of them won.
    """
    for p in (before, after):
        if not Path(p).is_file():
            return Comparison(error=f"missing screenshot: {p}")
    try:
        forward = _ask_pair(before, after, model, timeout)   # A=before B=after
        reverse = _ask_pair(after, before, model, timeout)   # A=after B=before
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError) as exc:
        return Comparison(error=f"vision model unreachable: {exc}")

    says_after = (forward == "B", reverse == "A")
    says_before = (forward == "A", reverse == "B")
    if all(says_after):
        return Comparison(better="after", agreed=True, assessed=True,
                          why="preferred in both orderings")
    if all(says_before):
        return Comparison(better="before", agreed=True, assessed=True,
                          why="preferred in both orderings")
    return Comparison(better="same", agreed=False, assessed=True,
                      why=f"the two orderings disagreed ({forward}/{reverse})")


def review(root: Path | str, *, model: str = DEFAULT_MODEL,
           threshold: float = 6.5, page: str = "index.html") -> Critique:
    """Screenshot a built app and critique it. Never raises."""
    shot = Path(tempfile.mkdtemp(prefix="ledger-vision-")) / "shot.png"
    if not screenshot(root, shot, page=page):
        return Critique(error="could not photograph the build "
                              "(no headless Chrome, or no index.html)")
    return critique_image(shot, model=model, threshold=threshold)
