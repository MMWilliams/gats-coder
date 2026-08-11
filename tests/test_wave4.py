"""Wave-4 parity (Claude Code / Kimi doc study): action hooks, ordered
policy rules with journaled reasons, per-invocation --deny scope, doctor
config lint, per-agent session backends."""

import json
import sys

from conftest import GOAL, TEST_CMD, needs_git, queue_fix

import ledger.cli as cli
from ledger.executor import PolicyGate, run_repo_episode
from ledger.journal import Journal
from ledger.llm import FakeBackend
from ledger.planner import PlanConfig
from ledger.session import AgentSpec, Session

FIX_DIFF = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n-    return a - b\n+    return a + b\n")


def _responder(prompt: str) -> str:
    if "REPOSITORY FILES" in prompt:
        return FIX_DIFF
    return json.dumps({"goal": ["tests.pass:unit", "repo.clean"],
                       "rationale": "green and committed"})


# -- hooks -------------------------------------------------------------------

@needs_git
def test_pre_action_gate_hook_blocks(repo, tmp_path):
    episode_dir = tmp_path / "hookblock"
    queue_fix(episode_dir)
    hooks = [{"event": "pre_action", "match": "Commit", "gate": True,
              "command": [sys.executable, "-c", "raise SystemExit(1)"]}]
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD, hooks=hooks)

    assert not result.success
    assert executor.denials > 0
    entries = Journal.read(str(journal_path))
    hook_entries = [e for e in entries if e["t"] == "HOOK"]
    assert hook_entries and all(e["action"] == "Commit" for e in hook_entries)
    assert all(e["blocked"] for e in hook_entries)
    esc = [e for e in entries if e["t"] == "ESCALATION"]
    assert esc and esc[0]["reason"] == "gate_denied"


@needs_git
def test_post_action_hook_observes_everything(repo, tmp_path, monkeypatch):
    marker = tmp_path / "hook-touched.txt"
    monkeypatch.setenv("HOOK_MARKER", str(marker))
    episode_dir = tmp_path / "hookpost"
    queue_fix(episode_dir)
    hooks = [{"event": "post_action", "match": "*", "command": [
        sys.executable, "-c",
        "import os, pathlib; "
        "p = pathlib.Path(os.environ['HOOK_MARKER']); "
        "p.open('a').write(os.environ['LEDGER_ACTION'] + '\\n')"]}]
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD, hooks=hooks)

    assert result.success
    touched = marker.read_text(encoding="utf-8").splitlines()
    assert set(touched) == set(result.plan)  # hook saw every executed action
    entries = Journal.read(str(journal_path))
    posts = [e for e in entries if e["t"] == "HOOK"]
    assert posts and all(e["event"] == "post_action" and e["exit"] == 0
                         and not e["blocked"] for e in posts)


# -- ordered rules + reasons -------------------------------------------------

@needs_git
def test_gate_reason_is_journaled(repo, tmp_path):
    episode_dir = tmp_path / "why-denied"
    queue_fix(episode_dir)
    gate = PolicyGate("autonomous", rules=[
        {"action": "Commit", "decision": "deny",
         "reason": "commits forbidden in CI"}])
    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD, gate=gate, profile="autonomous")

    assert not result.success
    entries = Journal.read(str(journal_path))
    denied = [e for e in entries
              if e["t"] == "GATE" and not e["approved"]]
    assert denied
    assert all(e["reason"] == "commits forbidden in CI" for e in denied)


def test_ask_rule_forces_approver_on_ungated_risk():
    consulted = []

    def approver(name, risk):
        consulted.append(name)
        return True

    gate = PolicyGate("autonomous", approver=approver,
                      rules=[{"action": "RunSuite", "decision": "ask"}])
    assert gate("RunSuite", "exec")     # autonomous would not gate exec...
    assert consulted == ["RunSuite"]    # ...but the ask rule forced a prompt


def test_deny_flag_outranks_config_allow(tmp_path):
    class A:
        profile = "attended"
        yes = True
        deny = "Commit"
        allow = None

    cfg = {"policy": {"allow": ["Commit"]}}
    gate, _ = cli._gate_and_profile(A(), cfg)
    assert not gate("Commit", "write")          # flag scope wins
    assert gate.last_reason == "--deny flag"


# -- doctor config lint ------------------------------------------------------

@needs_git
def test_doctor_lints_config(repo, capsys):
    d = repo / ".ledger"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({
        "test_cmd": ["pytest"],
        "profile": "cowboy",
        "typo_key": 1,
        "hooks": [{"event": "sometimes", "command": ["x"]}],
    }), encoding="utf-8")
    rc = cli.main(["doctor", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "unknown key 'typo_key'" in out
    assert "unknown profile 'cowboy'" in out
    assert "pre_action or post_action" in out


@needs_git
def test_doctor_clean_config_passes_lint(repo, capsys):
    d = repo / ".ledger"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({
        "test_cmd": ["pytest", "-q"],
        "planner": PlanConfig().to_dict(),
        "profile": "attended",
        "slots": ["a", "b"],
        "max_author_attempts": 6,
        "policy": {"deny": ["PushBranch"]},
        "hooks": [{"event": "post_action", "command": ["echo", "hi"]}],
    }), encoding="utf-8")
    cli.main(["doctor", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert "config lint" in out and "clean" in out


# -- per-agent session backends ----------------------------------------------

@needs_git
def test_session_honors_policy_and_hooks(repo, tmp_path, monkeypatch, capsys):
    """A deny rule must mean the same thing for `session` as for `run` --
    and configured hooks must fire there too."""
    d = repo / ".ledger"
    d.mkdir()
    marker = tmp_path / "hook-ran.txt"
    monkeypatch.setenv("HOOK_MARKER", str(marker))
    (d / "config.json").write_text(json.dumps({
        "test_cmd": TEST_CMD,
        "policy": {"deny": ["Commit"]},
        "hooks": [{"event": "post_action", "match": "*", "command": [
            sys.executable, "-c",
            "import os, pathlib; "
            "pathlib.Path(os.environ['HOOK_MARKER']).open('a').write('x')"]}],
    }), encoding="utf-8")
    spec = tmp_path / "agents.json"
    spec.write_text(json.dumps([
        {"name": "a1", "repo": str(repo), "task": "fix the failing test"}]),
        encoding="utf-8")

    monkeypatch.setattr(cli, "_backend",
                        lambda args, cfg=None: FakeBackend(_responder))
    rc = cli.main(["session", str(spec), "--repo", str(repo),
                   "--out", str(tmp_path / "sess"), "--yes", "--workers", "1"])

    assert rc == 1                                    # Commit was denied
    assert marker.exists(), "configured hooks must fire inside sessions"
    journals = list((tmp_path / "sess").rglob("episode.jsonl"))
    assert journals
    entries = Journal.read(str(journals[0]))
    denied = [e for e in entries if e["t"] == "GATE" and not e["approved"]]
    assert denied and all(e["action"] == "Commit" for e in denied)


@needs_git
def test_session_per_agent_backend_override(repo, tmp_path):
    fast = FakeBackend(FIX_DIFF, id="fast")
    cheap = FakeBackend(FIX_DIFF, id="cheap")
    session = Session(tmp_path / "sess", cfg=PlanConfig(),
                      backend=FakeBackend("unused fallback"))
    goal = frozenset({"tests.pass:unit"})
    specs = [
        AgentSpec(name="agent-fast", repo_root=repo, test_cmd=TEST_CMD,
                  goal=goal, slots=["a"], backend=fast),
        AgentSpec(name="agent-cheap", repo_root=repo, test_cmd=TEST_CMD,
                  goal=goal, slots=["a"], backend=cheap),
    ]
    outcomes = session.run_parallel(specs, max_workers=2)
    assert all(o.success for o in outcomes), [o.error for o in outcomes]
    assert fast.calls > 0 and cheap.calls > 0  # each agent used its own model
