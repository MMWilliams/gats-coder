

def test_python_dash_m_ledger_works_without_path():
    """The console script lands in a Scripts directory that is frequently
    not on PATH -- on Windows the failure reads "ledger is not recognized",
    as though the install had not happened. `python -m ledger` needs only
    the interpreter that did the installing, so it must keep working."""
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, "-m", "ledger", "--version"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "ledger" in proc.stdout.lower()


def test_no_output_glyph_escapes_the_ascii_fallback():
    """Regression: ui.py degrades the verdict glyphs to ASCII when the
    console can't carry them, but 44 lines across five modules printed a
    bare middle dot or em dash straight past that gate. Redirected output
    on Windows is cp1252, so those were written lossily -- and a character
    cp1252 lacks would have raised UnicodeEncodeError and killed the
    command outright, after all the work was already done.

    Checked per STATEMENT, not per line: the box-drawing set is gated by an
    `if ui.UNICODE else` that sits on the following line, and docstrings
    are prose rather than output.
    """
    import ast
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "ledger"
    offenders = []
    for path in sorted(root.glob("*.py")):
        if path.name == "vis.py":
            continue                   # writes a UTF-8 HTML file, not stdout
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if not any(ord(c) > 127 for c in node.value):
                continue
            if node.value in docstrings:
                continue
            # innermost statement that CONTAINS the literal -- the gate can
            # sit on a continuation line, as it does for the box-drawing set
            holders = [a for a in ast.walk(tree)
                       if isinstance(a, ast.stmt)
                       and a.lineno <= node.lineno
                       and (a.end_lineno or a.lineno)
                       >= (node.end_lineno or node.lineno)]
            stmt = min(holders,
                       key=lambda a: (a.end_lineno or a.lineno) - a.lineno,
                       default=None)
            segment = ast.get_source_segment(src, stmt) if stmt else ""
            if "UNICODE" in (segment or ""):
                continue               # has an ASCII branch alongside it
            if isinstance(stmt, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id.endswith("_U")
                    for t in stmt.targets):
                continue               # _U/_A pair, selected on UNICODE
            if re.search(r"\bin \(", segment or ""):
                continue               # a membership test, never printed
            if "re.sub" in (segment or ""):
                continue               # a pattern that strips glyphs, not output
            offenders.append(f"{path.name}:{node.lineno}: {node.value[:60]!r}")
    assert not offenders, (
        "these print non-ASCII without an ASCII fallback:\n"
        + "\n".join(offenders))
