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
import time
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


def frame_width() -> int:
    """One width for every framed element, so the session reads as a
    single panel rather than a pile of boxes."""
    return min(max(40, shutil.get_terminal_size().columns) - 1, 96)


def banner(repo: str, profile: str, backend_id: str) -> str:
    bar = "▌" if ui.UNICODE else "|"
    dot = " · " if ui.UNICODE else " - "
    line = "─" if ui.UNICODE else "-"
    head = (ui.accent(bar) + " " + ui.bold("LEDGER") + "  "
            + ui.dim(dot.join([repo, f"profile {profile}", backend_id])))
    tip = ui.dim("ask about the repo, or say what to change "
                 f"{ui.DASH} changes run as verified episodes")
    return "\n".join([head, tip, ui.dim(line * frame_width())])


# -- spinner -----------------------------------------------------------------

_FRAMES_U = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_FRAMES_A = "|/-\\"


class Progress:
    """The live status a long call reports through. Callers hold it and call
    it as they move between phases; the spinner thread renders whatever it
    currently says, so the line always names real work in progress."""

    def __init__(self, label: str):
        self.label = label
        self.started = time.monotonic()

    def __call__(self, text: str) -> None:
        """Switch the displayed phase (usable directly as a callback)."""
        self.label = text

    update = __call__

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def line(self, frame: str) -> str:
        secs = self.elapsed()
        clock = f"{secs:.0f}s" if secs < 60 \
            else f"{int(secs // 60)}m{int(secs % 60):02d}s"
        return (f"{ui.accent(frame)} {self.label} "
                f"{ui.dim(clock)}{ui.dim(f' {ui.SEP} ctrl+c to interrupt')}")


# The live indicator, so anything that needs the terminal (a gate asking
# permission) can make it stand aside. Without this the spinner repaints
# over the question every 80ms and the session looks hung while it is
# actually waiting for a keypress nobody can see.
_ACTIVE: dict | None = None


@contextmanager
def paused():
    """Suspend the working indicator so a prompt is visible and answerable."""
    global _ACTIVE
    record = _ACTIVE
    if record is None:
        yield
        return
    record["paused"] = True
    sys.stdout.write("\r\x1b[K\x1b[?25h")     # clear the line, show cursor
    sys.stdout.flush()
    try:
        yield
    finally:
        record["paused"] = False
        if interactive():
            sys.stdout.write("\x1b[?25l")
            sys.stdout.flush()


@contextmanager
def spinner(label: str):
    """Animated 'working' indicator around a blocking call. Yields a
    Progress handle so the caller can rename the phase mid-flight. Silent
    off-TTY (yields a handle that does nothing visible)."""
    global _ACTIVE
    handle = Progress(label)
    if not interactive():
        yield handle
        return
    frames = itertools.cycle(_FRAMES_U if ui.UNICODE else _FRAMES_A)
    stop = threading.Event()
    record = {"paused": False}
    previous, _ACTIVE = _ACTIVE, record

    def spin():
        while not stop.wait(0.08):
            if record["paused"]:
                continue
            sys.stdout.write("\r" + handle.line(next(frames)) + "\x1b[K")
            sys.stdout.flush()

    t = threading.Thread(target=spin, daemon=True)
    sys.stdout.write("\x1b[?25l")  # hide cursor
    sys.stdout.flush()
    t.start()
    try:
        yield handle
    finally:
        stop.set()
        t.join(timeout=0.5)
        _ACTIVE = previous
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

    def hint(self, budget: int = 10_000) -> str:
        """The bottom-frame text: completions when relevant, key help else.
        Never wider than `budget` visible columns -- the frame is only
        square if its contents respect it."""
        m = self.matches()
        if m:
            parts, used = [], 0
            for i, (c, d) in enumerate(m):
                cell = f"{c} {d}"
                if used + len(cell) + (2 if parts else 0) > budget:
                    if not parts:            # even one entry is too wide
                        cell = cell[:max(1, budget)]
                    else:
                        break
                used += len(cell) + (2 if parts else 0)
                parts.append(ui.bold(cell)
                             if i == self.completion_i % len(m)
                             else ui.dim(cell))
            return "  ".join(parts)
        full = (f"Tab completes /commands {ui.SEP} {ui.UP} history {ui.SEP} Esc clears"
                if ui.UNICODE else
                "Tab completes /commands - up = history - Esc clears")
        return ui.dim(full if len(full) <= budget else "Tab completes")


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

    def frame(self, title: str = "session") -> tuple[list[str], int]:
        """The three frame lines plus the cursor's 1-based screen column.
        Pure string work -- no terminal required, so it is testable."""
        width = frame_width()
        tl, tr, bl, br, h, v = ("╭", "╮", "╰", "╯", "─", "│") \
            if ui.UNICODE else ("+", "+", "+", "+", "-", "|")
        prompt = "❯ " if ui.UNICODE else "> "

        # inner = columns between the two verticals, minus one pad each side
        inner = width - 4
        room = inner - len(prompt)
        start = max(0, self.state.cursor - room + 1)
        shown = self.state.buffer[start:start + room]

        head = f"{tl}{h} {title} "
        top = head + h * max(1, width - len(head) - 1) + tr

        body_text = prompt + shown
        pad = " " * max(0, inner - len(body_text))
        mid = (ui.dim(v) + " " + ui.accent(prompt) + shown + pad + " "
               + ui.dim(v))

        hint = self.state.hint(budget=max(4, width - 6))
        hint_room = max(0, width - 6 - ui.visible_len(hint))
        foot = (ui.dim(bl + h) + " " + hint + " "
                + ui.dim(h * hint_room + h + br))
        # cursor: 1(border) + 1(pad) + prompt + offset into the window
        col = 3 + len(prompt) + (self.state.cursor - start)
        return [ui.dim(top), mid, foot], col

    def _draw(self, first: bool) -> None:
        lines, col = self.frame()
        out = sys.stdout
        if not first:
            # parked on the input line (frame line 2): top is ONE line up
            out.write("\r\x1b[1A")
        out.write(lines[0] + "\x1b[K\n" + lines[1] + "\x1b[K\n"
                  + lines[2] + "\x1b[K")
        # park the real cursor on the input line at the right column
        out.write(f"\x1b[1A\r\x1b[{col}G")
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
