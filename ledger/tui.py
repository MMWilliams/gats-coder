"""Interactive terminal UI for the unified session: a framed, styled input
field with live slash-command autocomplete, input history, and a working
spinner -- Codex-class ergonomics, stdlib-only.

Everything degrades: off-TTY there is no TUI at all (plain input()), and the
editor's logic lives in a console-free state machine (`EditorState`) so it
is testable without a terminal.
"""

from __future__ import annotations

import itertools
import shutil
import sys
import threading
from contextlib import contextmanager

from . import ui

COMMANDS = [
    ("/help", "show commands"),
    ("/do", "run a change episode directly"),
    ("/tools", "list connected MCP tools"),
    ("/context", "inline files into the conversation"),
    ("/tree", "refresh the repository file list"),
    ("/clear", "forget the conversation"),
    ("/save", "write the transcript to a file"),
    ("/exit", "leave the session"),
]


def interactive() -> bool:
    try:
        return (sys.stdin.isatty() and sys.stdout.isatty() and ui.COLOR)
    except (AttributeError, ValueError):
        return False


def banner(repo: str, profile: str, backend_id: str) -> str:
    bar = "▌" if ui.UNICODE else "|"
    head = (ui.accent(bar) + " " + ui.bold("LEDGER") + "  "
            + ui.dim(f"{repo} · profile {profile} · {backend_id}"))
    return head + "\n" + ui.rule()


# -- spinner -----------------------------------------------------------------

_FRAMES_U = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_FRAMES_A = "|/-\\"


@contextmanager
def spinner(label: str):
    """Animated 'working' indicator around a blocking call; silent off-TTY."""
    if not interactive():
        yield
        return
    frames = _FRAMES_U if ui.UNICODE else _FRAMES_A
    stop = threading.Event()

    def spin():
        for f in itertools.cycle(frames):
            if stop.wait(0.08):
                break
            sys.stdout.write(f"\r{ui.accent(f)} {ui.dim(label)}\x1b[K")
            sys.stdout.flush()

    t = threading.Thread(target=spin, daemon=True)
    sys.stdout.write("\x1b[?25l")  # hide cursor
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=0.5)
        sys.stdout.write("\r\x1b[K\x1b[?25h")  # clear line, show cursor
        sys.stdout.flush()


# -- editor state machine (console-free, tested) -----------------------------

class EditorState:
    """Line-editor logic: buffer, cursor, history, slash completion.
    `handle(key)` takes logical keys ('char:x', 'enter', 'backspace', 'tab',
    'up', 'down', 'left', 'right', 'home', 'end', 'esc') and returns the
    submitted string on 'enter', else None."""

    def __init__(self, commands=COMMANDS):
        self.commands = list(commands)
        self.history: list[str] = []
        self.buffer = ""
        self.cursor = 0
        self._hist_i: int | None = None
        self._pending = ""
        self.completion_i = 0
        self._comp_prefix: str | None = None  # what Tab-cycling filters on

    # -- completion ----------------------------------------------------------
    def matches(self) -> list[tuple[str, str]]:
        src = self._comp_prefix if self._comp_prefix is not None \
            else self.buffer
        if not src.startswith("/") or " " in src:
            return []
        return [(c, d) for c, d in self.commands if c.startswith(src)]

    def _complete(self) -> None:
        if self._comp_prefix is None:
            self._comp_prefix = self.buffer
        m = self.matches()
        if not m:
            self._comp_prefix = None
            return
        pick = m[self.completion_i % len(m)][0]
        if self.buffer == pick:  # already complete: cycle to the next
            self.completion_i += 1
            pick = m[self.completion_i % len(m)][0]
        self.buffer = pick
        self.cursor = len(self.buffer)

    # -- history -------------------------------------------------------------
    def _hist(self, direction: int) -> None:
        if not self.history:
            return
        if self._hist_i is None:
            if direction > 0:
                return
            self._pending = self.buffer
            self._hist_i = len(self.history) - 1
        else:
            self._hist_i += direction
        if self._hist_i >= len(self.history):
            self._hist_i = None
            self.buffer = self._pending
        elif self._hist_i < 0:
            self._hist_i = 0
        if self._hist_i is not None:
            self.buffer = self.history[self._hist_i]
        self.cursor = len(self.buffer)

    # -- keys ----------------------------------------------------------------
    def handle(self, key: str) -> str | None:
        if key not in ("tab",):
            self._comp_prefix = None
            if key != "enter":
                self.completion_i = 0 if key.startswith("char:") \
                    or key in ("backspace", "esc") else self.completion_i
        if key.startswith("char:"):
            ch = key[5:]
            self.buffer = (self.buffer[:self.cursor] + ch
                           + self.buffer[self.cursor:])
            self.cursor += len(ch)
            self.completion_i = 0
        elif key == "backspace" and self.cursor > 0:
            self.buffer = (self.buffer[:self.cursor - 1]
                           + self.buffer[self.cursor:])
            self.cursor -= 1
        elif key == "delete" and self.cursor < len(self.buffer):
            self.buffer = (self.buffer[:self.cursor]
                           + self.buffer[self.cursor + 1:])
        elif key == "left":
            self.cursor = max(0, self.cursor - 1)
        elif key == "right":
            self.cursor = min(len(self.buffer), self.cursor + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = len(self.buffer)
        elif key == "tab":
            self._complete()
        elif key == "up":
            self._hist(-1)
        elif key == "down":
            self._hist(+1)
        elif key == "esc":
            self.buffer, self.cursor = "", 0
        elif key == "enter":
            text = self.buffer
            if text.strip():
                self.history.append(text)
            self.buffer, self.cursor = "", 0
            self._hist_i, self._pending = None, ""
            self.completion_i = 0
            return text
        return None

    def hint(self) -> str:
        """The bottom-frame text: completions when relevant, key help else."""
        m = self.matches()
        if m:
            parts = []
            for i, (c, d) in enumerate(m[:4]):
                cell = f"{c} {d}"
                parts.append(ui.bold(cell)
                             if i == self.completion_i % len(m)
                             else ui.dim(cell))
            return "  ".join(parts)
        return ui.dim("Tab completes /commands · ↑ history · Esc clears"
                      if ui.UNICODE else
                      "Tab completes /commands - up = history - Esc clears")


# -- console layer -----------------------------------------------------------

def _read_key_windows() -> str:
    import msvcrt
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {"H": "up", "P": "down", "K": "left", "M": "right",
                "G": "home", "O": "end", "S": "delete"}.get(code, "none")
    if ch == "\r":
        return "enter"
    if ch == "\x08":
        return "backspace"
    if ch == "\t":
        return "tab"
    if ch == "\x1b":
        return "esc"
    if ch == "\x03":
        raise KeyboardInterrupt
    if ch == "\x04":
        raise EOFError
    if ch >= " ":
        return f"char:{ch}"
    return "none"


def _read_key_posix() -> str:
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            nxt = sys.stdin.read(1)
            if nxt == "[":
                code = sys.stdin.read(1)
                return {"A": "up", "B": "down", "D": "left", "C": "right",
                        "H": "home", "F": "end"}.get(code, "none")
            return "esc"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x7f":
        return "backspace"
    if ch == "\t":
        return "tab"
    if ch == "\x03":
        raise KeyboardInterrupt
    if ch == "\x04":
        raise EOFError
    if ch >= " ":
        return f"char:{ch}"
    return "none"


def _read_key() -> str:
    if sys.platform == "win32":
        return _read_key_windows()
    return _read_key_posix()


class LineEditor:
    """The framed input field. Draws a three-line frame, redraws per
    keypress, and collapses to a clean one-line `❯ text` after submit so
    the transcript stays tidy."""

    def __init__(self, commands=COMMANDS):
        self.state = EditorState(commands)

    def _draw(self, first: bool) -> None:
        cols = max(40, shutil.get_terminal_size().columns)
        width = min(cols - 2, 96)
        tl, tr, bl, br, h, v = ("╭", "╮", "╰", "╯", "─", "│") \
            if ui.UNICODE else ("+", "+", "+", "+", "-", "|")
        prompt = "❯ " if ui.UNICODE else "> "
        inner = width - 4
        buf = self.state.buffer
        # keep the cursor visible: window the buffer if it overflows
        start = max(0, self.state.cursor - (inner - len(prompt)))
        shown = buf[start:start + inner - len(prompt)]
        hint = self.state.hint()

        out = sys.stdout
        if not first:
            # parked on the input line (frame line 2): top is ONE line up
            out.write("\r\x1b[1A")
        top = ui.dim(tl + h + " session " + h * max(1, width - 12) + tr)
        mid = (ui.dim(v) + " " + ui.accent(prompt) + shown
               + "\x1b[K")
        bot = ui.dim(bl + h) + " " + hint + "\x1b[K"
        out.write(top + "\x1b[K\n" + mid + "\n" + bot)
        # park the real cursor on the input line at the right column
        col = 3 + len(prompt) + (self.state.cursor - start)
        out.write(f"\x1b[1A\x1b[{col}G")
        out.flush()

    def read(self, _prompt: str = "") -> str:
        # A non-default prompt is a question (episode confirmation etc.) --
        # it must be visible above the frame, not swallowed by it.
        if _prompt and _prompt.strip() and _prompt != "> ":
            sys.stdout.write(ui.warn(_prompt.strip()) + "\n")
        first = True
        self._draw(first)
        first = False
        while True:
            key = _read_key()
            if key == "none":
                continue
            try:
                result = self.state.handle(key)
            except Exception:
                result = None
            if result is not None:
                prompt = "❯ " if ui.UNICODE else "> "
                # collapse the frame into one clean transcript line
                sys.stdout.write("\r\x1b[1A\x1b[J"
                                 + ui.accent(prompt) + result + "\n")
                sys.stdout.flush()
                return result
            self._draw(first)
