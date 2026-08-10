"""Toy-repo sensors: deterministic propositions from a real directory."""

import shutil
import subprocess

import pytest

from ledger.sensors import SensorBank, file_exists_sensor, git_sensor

needs_git = pytest.mark.skipif(shutil.which("git") is None,
                               reason="git not available")


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=True)


def test_file_exists_sensor(tmp_path):
    (tmp_path / "present.txt").write_text("x", encoding="utf-8")
    bank = SensorBank([file_exists_sensor(["present.txt", "missing.txt"])])
    props = bank.probe(tmp_path)
    assert "file.exists:present.txt" in props
    assert all("missing" not in p for p in props)


@needs_git
def test_git_sensor_clean_and_dirty(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init")
    bank = SensorBank([git_sensor()])

    props = bank.probe(tmp_path)
    assert "repo.clean" in props
    assert any(p.startswith("repo.branch:") for p in props)

    (tmp_path / "untracked.txt").write_text("x", encoding="utf-8")
    assert "repo.clean" not in bank.probe(tmp_path)


def test_sensor_set_hash_is_stable():
    b1 = SensorBank([git_sensor(), file_exists_sensor(["a"])])
    b2 = SensorBank([file_exists_sensor(["a"]), git_sensor()])
    assert b1.set_hash() == b2.set_hash()  # order-independent identity
