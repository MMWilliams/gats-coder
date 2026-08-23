"""Sensor bank (design doc SS4.2): deterministic probes that turn a repo into
propositions. Only sensor output ever enters state -- never LLM claims.

M1 ships the toy-repo sensors (file existence, git cleanliness/branch);
build/test/lint sensors arrive with the executor in M2.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .state import Props

Probe = Callable[[Path], set]


@dataclass(frozen=True)
class Sensor:
    name: str
    version: str
    cost_tier: str  # "instant" | "cheap" | "expensive"
    probe: Probe


def file_exists_sensor(rel_paths: list[str]) -> Sensor:
    watched = tuple(sorted(rel_paths))

    def probe(root: Path) -> set:
        props = set()
        for rel in watched:
            if (root / rel).exists():
                props.add(f"file.exists:{rel.replace(chr(92), '/')}")
        return props

    return Sensor(name=f"file_exists[{','.join(watched)}]", version="1",
                  cost_tier="instant", probe=probe)


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


#: Agent-generated working branches are named `ledger/<episode>`. The episode
#: part is an identifier the agent invented, not a fact about the world.
AGENT_BRANCH_PREFIX = "ledger/"
AGENT_BRANCH_PROP = "repo.branch:ledger"


def git_sensor() -> Sensor:
    def probe(root: Path) -> set:
        props = set()
        status = _git(root, "status", "--porcelain")
        if status is not None:
            if status == "":
                props.add("repo.clean")
            branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
            if branch:
                # ABSTRACTION (design SS4.1, fixes SS12.8). A branch named
                # `ledger/alpha` and one named `ledger/beta` are the same
                # SITUATION -- "on an agent working branch" -- differing only
                # by an identifier the agent minted itself. Emitting the raw
                # name put that identifier inside k(s), which made every
                # episode's state keys unique and silently destroyed all
                # cross-agent and cross-episode graph reuse: two agents on the
                # same repo pursuing the same goal shared ZERO nodes.
                #
                # Real branches (main, develop, a user's feature branch) keep
                # their names, because *those* distinctions are semantic. Only
                # the agent's own scratch branches collapse to a constant.
                # Same principle as bucketing continuous values: the
                # abstraction must be coarse enough for states to merge.
                props.add(AGENT_BRANCH_PROP
                          if branch.startswith(AGENT_BRANCH_PREFIX)
                          else f"repo.branch:{branch}")
        return props

    return Sensor(name="git", version="2", cost_tier="instant", probe=probe)


class ExpensiveResultCache:
    """Memoization for expensive probes (design SS4.2): results keyed by
    content fingerprint. The *skill* that ran the expensive command deposits
    the result; the sensor only reads. On a cache miss the sensor emits
    nothing -- unknown status is the absence of a proposition, never a guess.
    """

    def __init__(self):
        self._by_fp: dict[str, frozenset] = {}

    def put(self, fingerprint: str, props: set | frozenset) -> None:
        self._by_fp[fingerprint] = frozenset(props)

    def get(self, fingerprint: str) -> frozenset | None:
        return self._by_fp.get(fingerprint)


def test_status_sensor(cache: ExpensiveResultCache,
                       fingerprint_fn: Callable[[Path], str],
                       suite: str = "unit") -> Sensor:
    def probe(root: Path) -> set:
        cached = cache.get(fingerprint_fn(root))
        return set(cached) if cached is not None else set()

    return Sensor(name=f"test_status[{suite}]", version="1",
                  cost_tier="expensive", probe=probe)


def design_sensor(cache: ExpensiveResultCache,
                  fingerprint_fn: Callable[[Path], str]) -> Sensor:
    """Emits `design.ok` when the last visual review of THIS content passed.

    Same shape as the test sensor, and for the same reason: the skill that
    paid for the expensive thing deposits the result, the sensor only
    reads, and a cache miss emits nothing rather than guessing. Keyed by
    content fingerprint, so changing a single line invalidates the verdict
    -- a review of an older build must never be mistaken for a review of
    this one.

    Deliberately its own proposition. A model's opinion is not the same
    kind of evidence as an executed test, so it is never allowed to stand
    in for `tests.pass`: something beautiful that fails its tests has not
    passed, and something correct that looks like a placeholder is not
    finished.
    """

    def probe(root: Path) -> set:
        cached = cache.get(fingerprint_fn(root))
        return set(cached) if cached is not None else set()

    return Sensor(name="design_review", version="1",
                  cost_tier="expensive", probe=probe)


def remote_branch_sensor(branch: str) -> Sensor:
    """Emits `repo.pushed:<branch>` iff origin actually has the branch --
    the push skill's claim is confirmed by asking the remote, never by
    trusting the exit code alone."""

    def probe(root: Path) -> set:
        out = _git(root, "ls-remote", "--heads", "origin", branch)
        return {f"repo.pushed:{branch}"} if out else set()

    return Sensor(name=f"remote_branch[{branch}]", version="1",
                  cost_tier="cheap", probe=probe)


def pr_sensor(branch: str) -> Sensor:
    """Emits `repo.pr:<branch>` iff `gh pr view <branch>` says a PR exists.
    Absent gh (or no PR yet) emits nothing -- unknown is never a guess."""
    import shutil as _shutil
    gh = _shutil.which("gh")

    def probe(root: Path) -> set:
        if gh is None:
            return set()
        try:
            proc = subprocess.run([gh, "pr", "view", branch, "--json", "url"],
                                  cwd=root, capture_output=True, text=True,
                                  timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return set()
        return {f"repo.pr:{branch}"} if proc.returncode == 0 else set()

    return Sensor(name=f"pr[{branch}]", version="1",
                  cost_tier="cheap", probe=probe)


def budget_sensor(meter) -> Sensor:
    """Emits `budget.<resource>:ok` while the meter has headroom (design
    SS12.3). Exhaustion is the absence of the proposition -- actions whose
    preconditions require it become symbolically inapplicable, which is how
    a spend ceiling turns into planner behavior instead of an exception."""

    def probe(root: Path) -> set:
        return set() if meter.exhausted else {meter.prop}

    return Sensor(name=f"budget[{meter.resource}]", version="1",
                  cost_tier="instant", probe=probe)


def dir_marker_sensor(label: str, prop_prefix: str, directory: Path,
                      suffix: str = ".patch") -> Sensor:
    """Emits <prop_prefix><stem> for each matching file in a directory
    (patch queue / applied-patch ledger)."""
    directory = Path(directory)

    def probe(root: Path) -> set:
        if not directory.is_dir():
            return set()
        return {f"{prop_prefix}{f.stem}"
                for f in sorted(directory.iterdir())
                if f.is_file() and f.suffix == suffix}

    return Sensor(name=f"dir_marker[{label}]", version="1",
                  cost_tier="instant", probe=probe)


class SensorBank:
    def __init__(self, sensors: list[Sensor]):
        self.sensors = sorted(sensors, key=lambda s: s.name)

    def probe(self, root: str | Path) -> Props:
        root = Path(root)
        props: set = set()
        for sensor in self.sensors:
            props |= sensor.probe(root)
        return frozenset(props)

    def set_hash(self) -> str:
        """Stamped into journal entries so replay detects instrument drift."""
        blob = "\n".join(f"{s.name}@{s.version}" for s in self.sensors)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
