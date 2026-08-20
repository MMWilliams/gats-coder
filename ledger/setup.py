"""`ledger setup`: get a working install without a scavenger hunt.

Everything LEDGER can use is optional and independent -- a model backend
(hosted or local) and, if wanted, speech. Before this, turning any of it on
meant reading a wall of prose, finding a release page, and exporting an
environment variable that vanished with the shell.

The rules here:

* Nothing is installed without being asked. Every install prints the exact
  command first; a package manager pulling software onto someone's machine
  is not a thing to do quietly.
* Detection never lies about what it found. A dependency is present because
  it was located and, where it is cheap, actually run.
* It works the same on Windows and macOS. The commands differ, the flow
  does not.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field


def system() -> str:
    return {"Windows": "windows", "Darwin": "macos"}.get(
        platform.system(), "linux")


def package_manager() -> str | None:
    """The first package manager present, in order of how likely it is to
    already be set up on that platform."""
    order = {
        "windows": ["winget", "scoop", "choco"],
        "macos": ["brew", "port"],
        "linux": ["apt-get", "dnf", "pacman"],
    }[system()]
    for name in order:
        if shutil.which(name):
            return name
    return None


def _install_cmd(manager: str, pkg: dict) -> list[str] | None:
    name = pkg.get(manager)
    if not name:
        return None
    return {
        "winget": ["winget", "install", "--accept-package-agreements",
                   "--accept-source-agreements", "-e", "--id", name],
        "scoop": ["scoop", "install", name],
        "choco": ["choco", "install", "-y", name],
        "brew": ["brew", "install", name],
        "port": ["sudo", "port", "install", name],
        "apt-get": ["sudo", "apt-get", "install", "-y", name],
        "dnf": ["sudo", "dnf", "install", "-y", name],
        "pacman": ["sudo", "pacman", "-S", "--noconfirm", name],
    }.get(manager)


@dataclass
class Dependency:
    key: str
    label: str
    why: str
    #: package id per manager; absent means "not available there"
    packages: dict = field(default_factory=dict)
    #: fallback when no package manager has it
    manual: str = ""
    #: pip package, when that is the honest way to install it
    pip: str = ""

    def present(self) -> str | None:
        """Where it is, or None. Overridden for the odd ones."""
        return shutil.which(self.key)

    def install_command(self) -> list[str] | None:
        manager = package_manager()
        if manager:
            cmd = _install_cmd(manager, self.packages)
            if cmd:
                return cmd
        if self.pip:
            import sys
            return [sys.executable, "-m", "pip", "install", self.pip]
        return None


def _runs(resolved: str, *args: str, timeout: int = 30) -> bool:
    """Does this executable actually run?

    Invoked by its RESOLVED path, never by bare name: on Windows the npm
    shims are .CMD files, and CreateProcess does not apply PATHEXT without a
    shell, so `["claude", "--version"]` fails on a perfectly good install and
    setup reports it missing.
    """
    try:
        proc = subprocess.run([resolved, *args], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


class Ollama(Dependency):
    def present(self):
        found = shutil.which("ollama")
        if not found:
            return None
        # Installed is not the same as running: a stopped daemon fails at the
        # first model call, long after setup said everything was fine.
        return found if _runs(found, "list", timeout=20) else None


class ClaudeCLI(Dependency):
    def present(self):
        found = shutil.which("claude")
        if not found:
            return None
        # A broken npm auto-update leaves a stub that resolves on PATH and
        # fails on every call; --version is the cheapest way to catch it.
        return found if _runs(found, "--version") else None

    def install_command(self):
        if shutil.which("npm"):
            return ["npm", "install", "-g", "@anthropic-ai/claude-code"]
        return None


def core() -> list[Dependency]:
    return [
        Dependency("git", "git", "worktrees, diffs, and every episode sandbox",
                   packages={"winget": "Git.Git", "brew": "git",
                             "choco": "git", "scoop": "git",
                             "apt-get": "git", "dnf": "git", "pacman": "git"}),
    ]


def backends() -> list[Dependency]:
    return [
        ClaudeCLI("claude", "Claude CLI",
                  "use your existing Claude subscription, no API key"),
        Ollama("ollama", "Ollama",
               "run models locally, offline, no key and no per-token cost",
               packages={"winget": "Ollama.Ollama", "brew": "ollama",
                         "choco": "ollama", "scoop": "ollama"},
               manual="https://ollama.com/download"),
    ]


def voice() -> list[Dependency]:
    return [
        Dependency("ffmpeg", "ffmpeg", "records the microphone",
                   packages={"winget": "Gyan.FFmpeg", "brew": "ffmpeg",
                             "choco": "ffmpeg", "scoop": "ffmpeg",
                             "apt-get": "ffmpeg", "dnf": "ffmpeg",
                             "pacman": "ffmpeg"}),
        Dependency("piper", "piper", "speaks, offline", pip="piper-tts",
                   packages={"brew": "piper"}),
    ]


def run_install(cmd: list[str], echo=print) -> bool:
    """Run an install command, showing it first. True if it succeeded."""
    echo("  $ " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        echo(f"  failed: {exc}")
        return False
    return proc.returncode == 0


def ollama_models() -> list[str]:
    try:
        proc = subprocess.run(["ollama", "list"], capture_output=True,
                              text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines()[1:]:
        name = line.split()[0] if line.split() else ""
        if name:
            out.append(name)
    return out
