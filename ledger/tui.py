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
    ("/status", "model, permissions, session"),
    ("/sessions", "earlier sessions in this project"),
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


# The wordmark: three rows of half-blocks, 24 columns, LEDGER.
WORDMARK = (
    "█   █▀▀ █▀▄ █▀▀▀ █▀▀ █▀█",
    "█   █▀▀ █ █ █ ▀█ █▀▀ █▀▄",
    "▀▀▀ ▀▀▀ ▀▀  ▀▀▀▀ ▀▀▀ ▀ ▀",
) if ui.UNICODE else ("LEDGER",)


def _animate() -> bool:
    import os
    return interactive() and not os.environ.get("LEDGER_NO_ANIMATION") \
        and not os.environ.get("CI")


def banner(repo: str, profile: str, backend_id: str, *, skip: bool = False,
           session: str = "", resumed: tuple[str, int] | None = None,
           branch: str = "", phase: float = 0.0) -> str:
    """The session's first screen: the mark, then one line of facts that
    matter -- where you are, which model, and what it may do unasked."""
    from . import AGENT_VERSION
    sep = ui.dim(f" {ui.SEP} ")
    where = ui.bold(repo) + (ui.dim(" on ") + ui.accent(branch) if branch else "")
    facts = sep.join([where, ui.rgb(backend_id, "violet"),
                      ui.dim("profile ") + profile])
    if ui.UNICODE:
        mark = [ui.gradient(row, "violet", "cyan", bold=True, phase=phase)
                for row in WORDMARK]
    else:
        mark = [ui.bold("LEDGER")]
    tag = (ui.bold("LEDGER") + " " + ui.dim(f"v{AGENT_VERSION}") + "  "
           + ui.dim("audit-first coding agent: every claim checked, "
                    "every step on the record"))
    lines = [""] + ["  " + row for row in mark] + ["", "  " + tag, "  " + facts]
    if resumed:
        title, count = resumed
        lines.append("  " + ui.badge("resumed", "violet") + " "
                     + ui.console_safe(title) + ui.dim(f"  ({count} messages)"))
    if skip:
        lines.append("  " + ui.badge("PERMISSIONS OFF", "red") + " "
                     + ui.bad("every gate, prompt and policy rule is bypassed"))
    tip = ui.dim(f"  ask about the code, or say what to change {ui.SEP} "
                 f"/help {ui.SEP} /status")
    if session:
        tip += ui.dim(f" {ui.SEP} session {session[-4:]}")
    lines += [tip, ui.dim(("─" if ui.UNICODE else "-") * frame_width())]
    return "\n".join(lines)


def show_banner(**kwargs) -> None:
    """Print the banner, with a quarter-second colour sweep across the mark
    on a real terminal (LEDGER_NO_ANIMATION=1 or CI turns it off)."""
    text = banner(**kwargs)
    if not (_animate() and ui.UNICODE and ui.COLOR):
        print(text)
        return
    height = len(WORDMARK) + 1
    sys.stdout.write("\x1b[?25l")
    try:
        for step in range(14):
            frame = banner(**{**kwargs, "phase": 1.0 - step / 13})
            rows = frame.split("\n")[:height]
            if step:
                sys.stdout.write(f"\x1b[{height}A")
            sys.stdout.write("\n".join(r + "\x1b[K" for r in rows) + "\n")
            sys.stdout.flush()
            time.sleep(0.018)
        sys.stdout.write(f"\x1b[{height}A")
        sys.stdout.write(text + "\n")
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


# -- rendering model replies -----------------------------------------------------

_KEYWORDS = {
    "python": "and as assert async await break class continue def del elif else "
              "except finally for from global if import in is lambda None "
              "nonlocal not or pass raise return True False try while with yield "
              "match case self",
    "js": "async await break case catch class const continue default delete do "
          "else export extends false finally for from function if import in "
          "instanceof let new null return super switch this throw true try "
          "typeof undefined var void while yield interface type enum implements",
    "rust": "as async await break const continue crate dyn else enum extern false "
            "fn for if impl in let loop match mod move mut pub ref return self "
            "Self static struct super trait true type unsafe use where while",
    "go": "break case chan const continue default defer else fallthrough for func "
          "go goto if import interface map package range return select struct "
          "switch type var nil true false",
    "sh": "if then else elif fi for while do done case esac function in export "
          "local return echo cd",
}
_ALIASES = {"py": "python", "python3": "python", "javascript": "js", "ts": "js",
            "typescript": "js", "jsx": "js", "tsx": "js", "rs": "rust",
            "bash": "sh", "shell": "sh", "zsh": "sh", "console": "sh",
            "golang": "go"}


def highlight(code: str, lang: str) -> str:
    """One line of code, coloured: keywords, strings, comments, numbers.
    A regex tokenizer, not a parser -- enough to read by, never wrong in a
    way that changes what the code says (the text itself is untouched)."""
    import re
    lang = _ALIASES.get(lang.lower(), lang.lower())
    if not ui.COLOR or lang not in _KEYWORDS and lang not in ("json", "toml",
                                                               "yaml", "c", "cpp",
                                                               "java"):
        return code
    words = set(_KEYWORDS.get(lang, _KEYWORDS["js"]).split())
    comment = r"#.*" if lang in ("python", "sh", "toml", "yaml") else r"//.*"
    token = re.compile(
        rf"(?P<c>{comment})|(?P<s>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`)"
        r"|(?P<n>\b\d[\d_.]*\b)|(?P<w>\b[A-Za-z_][A-Za-z0-9_]*\b)")
    out, last = [], 0
    for m in token.finditer(code):
        out.append(code[last:m.start()])
        text = m.group(0)
        if m.group("c"):
            out.append(ui.rgb(text, "gray"))
        elif m.group("s"):
            out.append(ui.rgb(text, "green"))
        elif m.group("n"):
            out.append(ui.rgb(text, "amber"))
        elif text in words:
            out.append(ui.rgb(text, "pink"))
        elif code[m.end():m.end() + 1] == "(":
            out.append(ui.rgb(text, "cyan"))
        else:
            out.append(text)
        last = m.end()
    out.append(code[last:])
    return "".join(out)


def _inline(text: str) -> str:
    import re
    text = re.sub(r"`([^`]+)`", lambda m: ui.rgb(m.group(1), "cyan"), text)
    return re.sub(r"\*\*([^*]+)\*\*", lambda m: ui.bold(m.group(1)), text)


def render(text: str) -> str:
    """A model reply as the terminal should show it: fenced code framed and
    highlighted, diffs in red and green, headings and bullets styled.
    Identity when colour is off, so piped output is exactly what was said."""
    if not ui.COLOR:
        return text
    bar = ui.rgb("│" if ui.UNICODE else "|", "slate")
    out, lang, in_code = [], "", False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code:
                in_code, lang = True, stripped[3:].strip()
                label = ui.rgb(f" {lang or 'code'} ", "gray")
                out.append(ui.rgb("╭─" if ui.UNICODE else "+-", "slate") + label)
            else:
                in_code = False
                out.append(ui.rgb("╰─" if ui.UNICODE else "+-", "slate"))
            continue
        if in_code:
            if lang == "diff" or (not lang and line[:1] in "+-@" and line[:3] != "---"):
                color = {"+": "green", "-": "red", "@": "cyan"}.get(line[:1])
                body = ui.rgb(line, color) if color else line
            else:
                body = highlight(line, lang)
            out.append(f"{bar} {body}")
            continue
        if stripped.startswith("#"):
            out.append(ui.gradient(stripped.lstrip("#").strip(), "violet", "cyan",
                                   bold=True))
        elif stripped[:2] in ("- ", "* "):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + ui.rgb(ui.DOT if ui.UNICODE else "*", "violet")
                       + " " + _inline(stripped[2:]))
        else:
            out.append(_inline(line))
    return "\n".join(out)


def say(text: str = "") -> None:
    """The session's output function: replies rendered, everything safe for
    this console."""
    print(ui.console_safe(render(text)))


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
        # The frame drifts violet to cyan and back; a highlight sweeps the
        # label, so a long wait still visibly moves.
        t = (secs * 0.6) % 2.0
        t = t if t <= 1.0 else 2.0 - t
        tint = tuple(round(a + (b - a) * t) for a, b in
                     zip(ui.THEME["violet"], ui.THEME["cyan"]))
        return (f"{ui.rgb(frame, tint, bold=True)} {shimmer(self.label, secs)} "
                f"{ui.dim(clock)}{ui.dim(f' {ui.SEP} ctrl+c to interrupt')}")


def shimmer(text: str, secs: float) -> str:
    """`text` with a soft band of light passing over it."""
    if not ui.COLOR:
        return text
    head = (secs * 14) % (len(text) + 8) - 4
    base, glow = ui.THEME["gray"], ui.THEME["white"]
    out = []
    for i, ch in enumerate(text):
        k = max(0.0, 1.0 - abs(i - head) / 4.0)
        out.append(ui.rgb(ch, tuple(round(a + (b - a) * k)
                                    for a, b in zip(base, glow))))
    return "".join(out)


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

    def __init__(self, commands=COMMANDS, title: str = "session",
                 danger: bool = False):
        self.state = EditorState(commands)
        self.title = title
        self.danger = danger

    def frame(self, title: str | None = None) -> tuple[list[str], int]:
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

        title = self.title if title is None else title
        head = f"{tl}{h} {title} "
        top = head + h * max(1, width - len(head) - 1) + tr
        edge = "red" if self.danger else "violet"

        body_text = prompt + shown
        pad = " " * max(0, inner - len(body_text))
        mid = (ui.rgb(v, edge) + " " + ui.gradient(prompt, "violet", "cyan",
                                                    bold=True)
               + shown + pad + " " + ui.rgb(v, edge))

        hint = self.state.hint(budget=max(4, width - 6))
        hint_room = max(0, width - 6 - ui.visible_len(hint))
        foot = (ui.rgb(bl + h, edge) + " " + hint + " "
                + ui.rgb(h * hint_room + h + br, edge))
        # cursor: 1(border) + 1(pad) + prompt + offset into the window
        col = 3 + len(prompt) + (self.state.cursor - start)
        top_line = (ui.rgb(f"{tl}{h} ", edge)
                    + ui.rgb(title, edge, bold=True)
                    + ui.rgb(" " + h * max(1, width - len(head) - 1) + tr, edge))
        return [top_line, mid, foot], col

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
