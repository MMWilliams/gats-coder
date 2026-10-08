"""Guard the boundary between task failures and Ledger framework repair."""

import json
import subprocess

import pytest

from ledger.cli import _recent_failure_context
from ledger.self_heal import (_snapshot, allowed_paths, framework_error,
                              resource_exhaustion)
from ledger.skills_llm import failure_source_context, godot_error_doc_urls


def test_only_ledger_tracebacks_trigger_framework_repair():
    assert framework_error('File "C:\\work\\ledger\\cli.py", line 4, in main')
    assert not framework_error('File "C:\\work\\game\\ship.gd", line 4')
    assert not framework_error('File "C:\\work\\game\\tools\\generate_assets.py"')
    assert resource_exhaustion("OSError: [WinError 1455] The paging file is too small")
    assert resource_exhaustion("MemoryError")
    assert resource_exhaustion("OSError: [Errno 12] Cannot allocate memory")
    assert not resource_exhaustion("Parse Error: unknown Godot property")


def test_repair_changes_are_limited_to_implementation():
    assert allowed_paths(["ledger/cli.py", "ledger/self_heal.py"])
    assert not allowed_paths([])
    assert not allowed_paths(["tests/test_cli.py"])
    assert not allowed_paths(["ledger/../trusted_control/evaluation.py"])


def test_snapshot_preserves_dirty_source_without_changing_original(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "snapshot"
    source.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    (source / "ledger").mkdir()
    implementation = source / "ledger" / "sample.py"
    implementation.write_text("version = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    implementation.write_text("version = 2\n", encoding="utf-8")
    (source / "ledger" / "new.py").write_text("new = True\n", encoding="utf-8")

    _snapshot(source, target)

    assert (target / "ledger" / "sample.py").read_text() == "version = 2\n"
    assert (target / "ledger" / "new.py").exists()
    assert implementation.read_text() == "version = 2\n"


def test_snapshot_failure_surfaces_git_output(tmp_path):
    # A generic "could not enumerate" message with no detail was the whole
    # reason a real self-heal attempt was undiagnosable: it hid git's own
    # explanation of what went wrong.
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    target = tmp_path / "snapshot"
    target.mkdir()
    with pytest.raises(RuntimeError, match="could not enumerate") as excinfo:
        _snapshot(not_a_repo, target)
    assert len(str(excinfo.value)) > len(
        "could not enumerate Ledger source for repair: ")


def test_next_iteration_receives_exact_failure_evidence(tmp_path):
    journal = tmp_path / "episode.jsonl"
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    (blobs / "output.txt").write_text("Godot parse error at ship.gd:42\n",
                                      encoding="utf-8")
    journal.write_text(json.dumps({
        "t": "EXEC", "action": "RunSuite", "ok": False,
        "claims": ["unit suite failing"],
        "commands": [{"out": "output"}],
    }) + "\n", encoding="utf-8")

    context = _recent_failure_context(journal)
    assert "Godot parse error at ship.gd:42" in context
    assert "RunSuite" in context


def test_godot_api_failures_point_to_official_class_docs():
    output = ('SCRIPT ERROR: Identifier "CPUParticlesMaterial" not declared '
              'at res://scripts/starfield.gd:12\n'
              'Cannot find member "FOO" in base "OmniLight3D"')
    assert godot_error_doc_urls(output) == [
        "https://docs.godotengine.org/en/stable/classes/class_cpuparticlesmaterial.html",
        "https://docs.godotengine.org/en/stable/classes/class_omnilight3d.html",
    ]
    assert godot_error_doc_urls(
        'res://scripts/starfield.gd:45: add_surface_from_arrays() needs Array'
    )[0] == "https://docs.godotengine.org/en/4.7/classes/class_arraymesh.html"


def test_godot_error_inlines_source_around_res_path(tmp_path):
    scene = tmp_path / "game" / "scenes" / "main.tscn"
    scene.parent.mkdir(parents=True)
    scene.write_text("one\ntwo\ninvalid_node\nfour\n", encoding="utf-8")
    context = failure_source_context(
        tmp_path, "Parse error. [Resource file res://scenes/main.tscn:3]",
        radius=1)
    assert "game\\scenes\\main.tscn:3" in context or "game/scenes/main.tscn:3" in context
    assert "3: invalid_node" in context
