"""Bundle-check a proposed patch with the project's own esbuild.

The syntax and import gates in skills_llm are pattern-level; what they
cannot see (assigning to a const, an import cycle's missing binding, JSX in
a .js file) the bundler rejects. Web projects built with Vite already carry
esbuild in node_modules, so the check costs no install: copy the tracked
tree to a scratch dir, overlay the proposed files, link node_modules, bundle
the page's module entries, discard the output.

Silent (returns []) whenever it cannot run faithfully: no esbuild, no HTML
entry, a copy that fails. A check that guesses would reject good patches.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_MODULE_SCRIPT = re.compile(
    r"<script\b[^>]*\btype=[\"']module[\"'][^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)
_ASSET_LOADERS = ("css", "png", "jpg", "jpeg", "gif", "svg", "webp", "glb",
                  "gltf", "hdr", "wav", "mp3", "ogg", "woff", "woff2", "ttf",
                  "json")


def _esbuild(root: Path) -> list[str] | None:
    script = root / "node_modules" / "esbuild" / "bin" / "esbuild"
    node = shutil.which("node")
    return [node, str(script)] if node and script.is_file() else None


def _link(target: Path, link: Path) -> bool:
    try:
        if sys.platform == "win32":
            # A junction needs no privilege, unlike a symlink.
            return subprocess.run(["cmd", "/c", "mklink", "/J", str(link),
                                   str(target)], capture_output=True,
                                  timeout=30).returncode == 0
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def _unlink(link: Path) -> None:
    # rmdir removes a junction/symlink without touching what it points to.
    try:
        os.rmdir(link) if sys.platform == "win32" else os.unlink(link)
    except OSError:
        pass


_SKIP_DIRS = {"node_modules", ".git", ".ledger", "dist", "build"}


def _tree_files(root: Path) -> list[str] | None:
    listed = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                            text=True, timeout=60)
    if listed.returncode == 0:
        return [r for r in listed.stdout.split("\n")
                if r and not r.startswith("node_modules/")]
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        out += [Path(dirpath, f).relative_to(root).as_posix() for f in filenames]
    return out


def bundle_errors(root: Path, files: list[tuple[str, str]],
                  timeout: int = 120) -> list[str]:
    root = Path(root)
    cmd = _esbuild(root)
    written = dict(files)
    html = written.get("index.html")
    if html is None and (root / "index.html").is_file():
        html = (root / "index.html").read_text(encoding="utf-8", errors="replace")
    if cmd is None or html is None:
        return []
    entries = [src.lstrip("/") for src in _MODULE_SCRIPT.findall(html)
               if not src.startswith(("http:", "https:", "//"))]
    if not entries:
        return []
    tracked = _tree_files(root)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "tree"
        work.mkdir()
        link = work / "node_modules"
        try:
            for rel in tracked:
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / rel, dst)
            for rel, body in files:
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(body, encoding="utf-8")
        except OSError:
            return []
        if not all((work / e).is_file() for e in entries):
            return [f"index.html loads {e}, which does not exist"
                    for e in entries if not (work / e).is_file()]
        if not _link(root / "node_modules", link):
            return []
        try:
            proc = subprocess.run(
                cmd + entries + ["--bundle", "--format=esm", "--log-level=error",
                                 f"--outdir={Path(tmp) / 'out'}",
                                 *(f"--loader:.{ext}=empty" for ext in _ASSET_LOADERS)],
                cwd=work, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout)
        finally:
            _unlink(link)
    if proc.returncode == 0:
        return []
    from .executor import failure_excerpt
    return [failure_excerpt(proc.stderr, limit=1500).strip()
            or "esbuild failed without output"]
