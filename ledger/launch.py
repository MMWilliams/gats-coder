"""Run the thing that was just built (design SS13).

An episode that ends with "goal reached" and a journal path is a claim you
still have to go and check by hand. The point of finishing a build is to
look at it, so on success LEDGER can start it and open it.

Two rules, both deliberate:

* Only on success. Launching a failed build wastes the human's attention on
  something already known to be broken, and it would bury the escalation
  under a browser window.
* Never guess destructively. Detection only ever *serves* files or runs a
  script the project already declares; it will not install anything, and an
  explicit command always wins over detection.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Launch:
    """How to start a project. `url` is opened once `command` is up."""
    kind: str
    command: list[str] | None
    url: str | None
    cwd: Path
    note: str


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _npm_script(root: Path) -> str | None:
    pkg = root / "package.json"
    if not pkg.is_file():
        return None
    try:
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    except (ValueError, OSError):
        return None
    for name in ("dev", "start", "serve", "preview"):
        if name in scripts:
            return name
    return None


def detect(root: Path | str) -> Launch | None:
    """Work out how to start whatever is in `root`, or None if unclear.

    Ordered by how explicit the project is about its own entry point: a
    declared npm script beats a guess, and a guess beats nothing.
    """
    root = Path(root)

    script = _npm_script(root)
    if script and shutil.which("npm"):
        # The dev server prints its own URL and the port is not ours to
        # predict, so no url here -- npm opens or reports it.
        return Launch(kind="npm", command=["npm", "run", script], url=None,
                      cwd=root, note=f"npm run {script}")

    index = root / "index.html"
    if index.is_file():
        # Served over http, never opened as file:// -- ES modules are blocked
        # on the file:// origin, so a double-click shows a blank page and
        # looks exactly like a broken build.
        port = _free_port()
        return Launch(
            kind="static",
            command=[sys.executable, "-m", "http.server", str(port),
                     "--bind", "127.0.0.1"],
            url=f"http://127.0.0.1:{port}/",
            cwd=root,
            note=f"python -m http.server {port}")

    return None


def launch(root: Path | str, command: str | None = None,
           open_browser: bool = True) -> Launch | None:
    """Start the project and return what was started, or None.

    The server is left running on purpose: the caller's process is about to
    exit and the human wants the thing to still be up when it does.
    """
    root = Path(root)
    if command:
        plan = Launch(kind="explicit", command=None, url=None, cwd=root,
                      note=command)
    else:
        plan = detect(root)
        if plan is None:
            return None

    try:
        if plan.kind == "explicit":
            # A user-supplied string goes through the shell so that pipes and
            # && behave the way they do when typed.
            subprocess.Popen(plan.note, cwd=str(root), shell=True)
        else:
            subprocess.Popen(plan.command, cwd=str(root),
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except OSError:
        return None

    if plan.url and open_browser:
        try:
            webbrowser.open(plan.url)
        except Exception:            # a headless box has no browser; fine
            pass
    return plan
