"""The bundle gate: the project's own esbuild, over the tree as it will be
after the patch. Skipped where esbuild is not installed."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ledger.bundle_check import bundle_errors

ESBUILD_SRC = Path(r"C:/Users/reese/ledger-chess/.ledger/episodes/chess-loop"
                   r"/worktree/node_modules")


def _project(tmp_path: Path) -> Path:
    if (sys.platform != "win32" or not shutil.which("node")
            or not (ESBUILD_SRC / "esbuild").is_dir()):
        pytest.skip("no esbuild install to borrow")
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text(
        '<script type="module" src="/src/main.js"></script>\n', encoding="utf-8")
    (root / "src" / "main.js").write_text(
        "import { add } from './math.js';\nconsole.log(add(1, 2));\n",
        encoding="utf-8")
    (root / "src" / "math.js").write_text(
        "export function add(a, b) { return a + b; }\n", encoding="utf-8")
    nm = root / "node_modules"
    nm.mkdir()
    for pkg in ("esbuild", "@esbuild"):
        subprocess.run(["cmd", "/c", "mklink", "/J", str(nm / pkg),
                        str(ESBUILD_SRC / pkg)], capture_output=True, check=True)
    return root


def test_a_const_reassignment_is_caught(tmp_path):
    """Vite built this and the page crashed on it (chess r10, r12, r24...)."""
    root = _project(tmp_path)
    bad = ("export function add(a, b) {\n  const best = a;\n"
           "  if (b > best) best = b;\n  return a + b;\n}\n")
    errs = bundle_errors(root, [("src/math.js", bad)])
    assert errs and 'Cannot assign to "best"' in errs[0]


def test_a_sound_patch_passes_and_the_tree_is_untouched(tmp_path):
    root = _project(tmp_path)
    good = "export function add(a, b) { return b + a; }\n"
    assert bundle_errors(root, [("src/math.js", good)]) == []
    assert "a + b" in (root / "src" / "math.js").read_text(encoding="utf-8")
    assert (root / "node_modules" / "esbuild").is_dir()   # link target intact


def test_no_entry_or_no_esbuild_means_no_opinion(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    assert bundle_errors(root, [("x.js", "const a = 1; a = 2;\n")]) == []
