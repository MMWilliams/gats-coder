"""Terminal styling, stdlib-only (the package has zero dependencies and
keeps it that way).

Color is a *presentation* concern: it activates only on an interactive
terminal, so piped output, CI logs, and tests see exactly the plain text
they always did. Honors NO_COLOR (https://no-color.org), TERM=dumb, and
LEDGER_COLOR=0/1 as an explicit override.
"""

from __future__ import annotations

import os
import sys

_CODES = {
    "bold": "1", "dim": "2", "italic": "3",
    "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36", "white": "37", "gray": "90",
    "bright_red": "91", "bright_green": "92", "bright_yellow": "93",
    "bright_cyan": "96",
}


def _enable_windows_vt() -> bool:
    """Turn on ANSI processing for this console. Returns whether escapes
    will actually render."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        # Declare the signatures: HANDLE is pointer-sized, and ctypes
        # defaults an undeclared restype to C int -- which SILENTLY
        # truncates the handle on 64-bit Windows, making every subsequent
        # console call fail and colour look "unsupported".
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD)]
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]

        handle = kernel32.GetStdHandle(wintypes.DWORD(-11 & 0xFFFFFFFF))
        if not handle or handle == wintypes.HANDLE(-1).value:
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        VT = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & VT:
            return True                      # already on (Windows Terminal)
        return bool(kernel32.SetConsoleMode(handle, mode.value | VT))
    except Exception:
        return False


def _terminal_declares_ansi() -> bool:
    """Environment signals from terminals that always speak ANSI, used when
    the console API is unavailable (e.g. output through a pty shim)."""
    return bool(os.environ.get("WT_SESSION")          # Windows Terminal
                or os.environ.get("ConEmuANSI") == "ON"
                or os.environ.get("ANSICON")
                or os.environ.get("TERM_PROGRAM")
                or (os.environ.get("TERM", "") not in ("", "dumb")
                    and os.name != "nt"))


def _detect_color() -> bool:
    override = os.environ.get("LEDGER_COLOR")
    if override == "0":
        return False
    if override == "1":
        _enable_windows_vt()
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        if not sys.stdout.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    return _enable_windows_vt() or _terminal_declares_ansi()


def _detect_unicode() -> bool:
    enc = getattr(sys.stdout, "encoding", "") or ""
    return "utf" in enc.lower()


COLOR = _detect_color()
UNICODE = _detect_unicode()


def _detect_truecolor() -> bool:
    """24-bit colour where the terminal says so; 256-colour otherwise
    (every terminal that speaks ANSI at all speaks that)."""
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    return bool(os.environ.get("WT_SESSION")) or os.environ.get(
        "TERM_PROGRAM") in ("iTerm.app", "vscode", "WezTerm", "ghostty", "Hyper")


TRUECOLOR = _detect_truecolor()

# The palette. Violet into cyan is LEDGER's mark; the rest carry meaning
# (green verified, amber attention, red refused) and never decoration.
THEME = {
    "violet": (139, 92, 246), "indigo": (99, 102, 241), "cyan": (34, 211, 238),
    "green": (74, 222, 128), "amber": (251, 191, 36), "red": (248, 113, 113),
    "pink": (244, 114, 182), "gray": (125, 125, 140), "white": (236, 236, 244),
    "slate": (71, 85, 105),
}


def _sgr(color: tuple[int, int, int], bg: bool = False) -> str:
    r, g, b = color
    if TRUECOLOR:
        return f"{48 if bg else 38};2;{r};{g};{b}"
    n = 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)
    return f"{48 if bg else 38};5;{n}"


def rgb(text: str, color, *, bold: bool = False, bg=None) -> str:
    """Text in a theme colour (a THEME name or an (r, g, b) triple)."""
    if not COLOR or not text:
        return text
    fg = THEME[color] if isinstance(color, str) else color
    codes = [_sgr(fg)]
    if bg is not None:
        codes.append(_sgr(THEME[bg] if isinstance(bg, str) else bg, bg=True))
    if bold:
        codes.insert(0, "1")
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def _mix(a, b, t: float):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient(text: str, start="violet", end="cyan", *, bold: bool = False,
             phase: float = 0.0) -> str:
    """`text` swept from one colour to another, character by character.
    `phase` rotates the sweep, which is all an animation needs."""
    if not COLOR or not text:
        return text
    a = THEME[start] if isinstance(start, str) else start
    b = THEME[end] if isinstance(end, str) else end
    n = max(1, len(text) - 1)
    out = []
    for i, ch in enumerate(text):
        if ch == " ":
            out.append(ch)
            continue
        t = (i / n + phase) % 2.0
        t = t if t <= 1.0 else 2.0 - t          # ping-pong, no hard seam
        out.append(f"\x1b[{'1;' if bold else ''}{_sgr(_mix(a, b, t))}m{ch}")
    return "".join(out) + "\x1b[0m"


def badge(text: str, kind: str = "violet") -> str:
    """A filled pill: ` PERMISSIONS OFF ` on red, ` resumed ` on violet."""
    if not COLOR:
        return f"[{text}]"
    return rgb(f" {text} ", (15, 15, 20), bold=True, bg=kind)


def diagnostics() -> list[tuple[str, str, bool]]:
    """(label, detail, healthy) rows explaining what this console got and
    why -- so 'the UI looks different in my terminal' is answerable."""
    try:
        tty = sys.stdout.isatty()
    except (AttributeError, ValueError):
        tty = False
    enc = getattr(sys.stdout, "encoding", None) or "unknown"
    forced = os.environ.get("LEDGER_COLOR")
    if COLOR:
        why = "forced by LEDGER_COLOR=1" if forced == "1" else "ANSI enabled"
    elif forced == "0":
        why = "off: LEDGER_COLOR=0"
    elif os.environ.get("NO_COLOR") is not None:
        why = "off: NO_COLOR is set"
    elif not tty:
        why = "off: output is not a terminal (piped or redirected)"
    else:
        why = "off: this console refused ANSI (try LEDGER_COLOR=1)"
    return [
        ("colour", why, COLOR),
        ("glyphs", f"{'unicode' if UNICODE else 'ascii fallback'} "
                   f"(encoding {enc})", UNICODE),
        ("interactive UI", "framed input + autocomplete" if (COLOR and tty)
         else "plain prompt (needs a terminal with colour)", COLOR and tty),
    ]

# Glyphs degrade to ASCII when the console encoding can't carry them.
OK = "✓" if UNICODE else "+"          # check mark
FAIL = "✗" if UNICODE else "x"        # ballot x
DOT = "●" if UNICODE else "*"         # filled circle
ARROW = "→" if UNICODE else "->"      # right arrow
CHOSEN = "◀" if UNICODE else "<=="    # left-pointing triangle
SEP = "·" if UNICODE else "-"         # inline separator
DASH = "—" if UNICODE else "--"       # em dash
UP = "↑" if UNICODE else "^"           # up arrow
MARK = "«" if UNICODE else "<<"        # points back at a row


def console_safe(text: str) -> str:
    """Text from somewhere else, made safe for THIS console.

    The glyph constants above are ours and degrade to ASCII, but model
    output, file contents and command output can hold anything. Printing a
    character the console cannot encode raises UnicodeEncodeError and kills
    the command -- on Windows a redirected stdout is cp1252, which has no
    check mark and no emoji. Losing a dash beats losing the run.

    NOT called `plain`: that name is taken below for ANSI stripping, and
    defining it twice made this function dead code the moment it was
    written -- the later definition simply won.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        return text.encode(encoding, "replace").decode(encoding, "replace")


def style(text: str, *names: str) -> str:
    """Wrap text in ANSI styles when color is active; identity otherwise."""
    if not COLOR or not names:
        return text
    codes = ";".join(_CODES[n] for n in names if n in _CODES)
    return f"\x1b[{codes}m{text}\x1b[0m" if codes else text


def ok(text: str) -> str:
    return rgb(text, "green")


def bad(text: str) -> str:
    return rgb(text, "red")


def warn(text: str) -> str:
    return rgb(text, "amber")


def accent(text: str) -> str:
    return rgb(text, "cyan")


def dim(text: str) -> str:
    return rgb(text, "gray")


def bold(text: str) -> str:
    return style(text, "bold")


def headline(success: bool, good_text: str, bad_text: str) -> str:
    """The one-line verdict every command leads with."""
    if success:
        return f"{ok(OK)} {bold(good_text)}"
    return f"{bad(FAIL)} {bold(bad_text)}"


def plan_chain(plan: list[str]) -> str:
    """A plan as a colored action chain: families accented, slots dimmed."""
    if not plan:
        return dim("(empty)")
    parts = []
    for action in plan:
        family, _, slot = action.partition(":")
        parts.append(accent(family) + (dim(":" + slot) if slot else ""))
    return f" {dim(ARROW)} ".join(parts)


def kv(label: str, value: str, width: int = 11) -> str:
    """Aligned key-value line with a dimmed label."""
    return f"  {dim(label.ljust(width))} {value}"


def rule(title: str = "") -> str:
    """A dimmed section rule, optionally titled."""
    line = "─" if UNICODE else "-"
    if not title:
        return dim(line * 44)
    pad = line * max(2, 42 - len(title))
    return dim(f"{line}{line} ") + bold(title) + dim(f" {pad}")


def plain(s: str) -> str:
    """`s` with ANSI escapes stripped -- the layout truth for any padding
    or width math, since escape bytes occupy no columns."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\x1b":
            j = s.find("m", i)
            i = (j + 1) if j != -1 else len(s)
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def visible_len(s: str) -> int:
    return len(plain(s))


def table(rows: list[list[str]], header: list[str] | None = None,
          highlight: int | None = None) -> str:
    """Aligned monospace table. Column widths come from the *plain* text so
    ANSI codes never skew the layout; `highlight` bolds one body row."""
    all_rows = ([header] if header else []) + rows
    widths = [max(len(plain(r[c])) for r in all_rows)
              for c in range(len(all_rows[0]))]

    def render(r: list[str], is_header: bool, hl: bool) -> str:
        cells = []
        for c, cell in enumerate(r):
            pad = " " * (widths[c] - len(plain(cell)))
            cells.append(cell + pad)
        text = "  " + "  ".join(cells).rstrip()
        if is_header:
            return dim(plain(text))
        return style(plain(text), "bold") if hl and COLOR else text

    lines = []
    if header:
        lines.append(render(header, True, False))
    for i, r in enumerate(rows):
        lines.append(render(r, False, i == highlight))
    return "\n".join(lines)


def panel(title: str, lines: list[str], *, color: str = "slate",
          width: int | None = None, title_color: str = "white") -> str:
    """A rounded box around `lines` with a title in its top edge. Widths come
    from the visible text, so styled content keeps the box square."""
    if width is None:
        import shutil
        width = min(max(40, shutil.get_terminal_size().columns) - 1, 96)
    tl, tr, bl, br, h, v = ("╭", "╮", "╰", "╯", "─", "│") if UNICODE         else ("+", "+", "+", "+", "-", "|")
    inner = width - 4
    head = f"{h} " + rgb(title, title_color, bold=True) + " " if title else h
    top = rgb(tl, color) + rgb(head[:1], color) + head[1:]         + rgb(h * max(1, width - 2 - visible_len(head)) + tr, color)
    out = [top]
    for line in lines:
        for chunk in _wrap_visible(line, inner):
            pad = " " * max(0, inner - visible_len(chunk))
            out.append(rgb(v, color) + " " + chunk + pad + " " + rgb(v, color))
    out.append(rgb(bl + h * (width - 2) + br, color))
    return "\n".join(out)


def _wrap_visible(line: str, room: int) -> list[str]:
    """Split a possibly styled line so no piece is wider than `room`.
    Plain lines wrap on spaces; styled ones are kept whole when they fit
    and otherwise cut on their plain text (losing the styling of that line
    beats a broken frame)."""
    if visible_len(line) <= room:
        return [line]
    import textwrap
    return textwrap.wrap(plain(line), room) or [""]


def next_steps(*commands: str) -> str:
    """A 'what to do next' block -- the helpful part of helpful output."""
    lines = [dim("next:")]
    for c in commands:
        lines.append(f"  {dim(DOT)} {c}")
    return "\n".join(lines)
