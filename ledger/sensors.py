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


def git_sensor() -> Sensor:
    def probe(root: Path) -> set:
        props = set()
        status = _git(root, "status", "--porcelain")
        if status is not None:
            if status == "":
                props.add("repo.clean")
            branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
            if branch:
                props.add(f"repo.branch:{branch}")
        return props

    return Sensor(name="git", version="1", cost_tier="instant", probe=probe)


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
