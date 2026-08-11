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


def style(text: str, *names: str) -> str:
    """Wrap text in ANSI styles when color is active; identity otherwise."""
    if not COLOR or not names:
        return text
    codes = ";".join(_CODES[n] for n in names if n in _CODES)
    return f"\x1b[{codes}m{text}\x1b[0m" if codes else text


def ok(text: str) -> str:
    return style(text, "bright_green")


def bad(text: str) -> str:
    return style(text, "bright_red")


def warn(text: str) -> str:
    return style(text, "bright_yellow")


def accent(text: str) -> str:
    return style(text, "bright_cyan")


def dim(text: str) -> str:
    return style(text, "gray")


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


def next_steps(*commands: str) -> str:
    """A 'what to do next' block -- the helpful part of helpful output."""
    lines = [dim("next:")]
    for c in commands:
        lines.append(f"  {dim(DOT)} {c}")
    return "\n".join(lines)
