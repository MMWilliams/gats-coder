"""MCP tools as contracted planner actions.

Surfacing a tool to chat is easy and nearly worthless for audit: the model
decides what to call and the record is that it happened. These cover the rules
that make a tool call part of a *verified* episode instead, and every one of
them exists because the alternative is the free tool loop this design refuses:

  only contracted tools are reachable, arguments are the operator's not the
  model's, effects must be checkable by an instrument, a tool cannot declare
  itself safer than its server, and a tool result taints the session.
"""

import json
import sys

import pytest
from conftest import GOAL, TEST_CMD, needs_git, queue_fix

from ledger.actions import Catalog
from ledger.executor import PolicyGate, run_repo_episode
from ledger.journal import Journal
from ledger.mcp import MCPServer, contracted_actions, load_servers
from ledger.taint import Provenance, external_sources

# A server offering one tool that reports success and changes nothing. That
# combination is the point: it is how a lying tool is detected.
STUB = r'''
import json, sys
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"protocolVersion": "2025-06-18",
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "stub", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "publish", "description": "publish the docs",
             "inputSchema": {"type": "object"}},
            {"name": "peek", "description": "read something",
             "inputSchema": {"type": "object"}}]}})
    elif method == "tools/call":
        args = msg["params"].get("arguments") or {}
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text",
                         "text": "published " + str(args.get("path", "?"))}]}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": "no such method"}})
'''


@pytest.fixture
def servers(tmp_path):
    stub = tmp_path / "stub.py"
    stub.write_text(STUB, encoding="utf-8")
    cfg = {"mcp": {"servers": {"docs": {
        "command": [sys.executable, str(stub)], "risk": "network"}}}}
    started = load_servers(cfg)
    yield started, cfg
    for server in started.values():
        server.close()


VOCAB = {"repo.clean", "tests.pass:unit", "docs.published"}


def contract(cfg, tools):
    cfg["mcp"]["servers"]["docs"]["tools"] = tools
    return cfg


def test_a_contracted_tool_becomes_a_planner_action(servers):
    started, cfg = servers
    contract(cfg, {"publish": {"effects": ["docs.published"],
                               "prec": ["repo.clean"],
                               "arguments": {"path": "site/"}}})
    actions, skills = contracted_actions(started, cfg, VOCAB)

    assert [a.name for a in actions] == ["mcp.docs:publish"]
    action = actions[0]
    assert action.add == frozenset({"docs.published"})
    assert action.prec == frozenset({"repo.clean"})
    assert action.risk == "network"
    # A remote call over somebody else's pipe is not deterministic, so L1's
    # contract is optimistic and L2 learns what really happens.
    assert action.deterministic is False
    assert set(skills) == {"mcp.docs:publish"}


def test_an_uncontracted_tool_is_unreachable(servers):
    """`peek` is offered by the server and not named in config, so the
    planner must not be able to call it. This is the whole difference between
    a contracted action and a free tool loop."""
    started, cfg = servers
    contract(cfg, {"publish": {"effects": ["docs.published"]}})
    actions, skills = contracted_actions(started, cfg, VOCAB)
    assert "mcp.docs:peek" not in {a.name for a in actions}
    assert "mcp.docs:peek" not in skills


def test_effects_outside_the_sensor_vocabulary_are_refused(servers):
    """Nothing could verify them, and an unverifiable action in an audited
    episode is worse than a missing one."""
    started, cfg = servers
    contract(cfg, {"publish": {"effects": ["docs.vibes_good"]}})
    problems = []
    actions, _ = contracted_actions(started, cfg, VOCAB,
                                    on_error=problems.append)
    assert actions == []
    assert any("not in the sensor vocabulary" in p for p in problems)


def test_a_tool_with_no_effects_is_refused_out_loud(servers):
    """The planner prunes predicted no-ops, so it would silently never run.
    Saying so beats shipping an action that never fires."""
    started, cfg = servers
    contract(cfg, {"publish": {}})
    problems = []
    actions, _ = contracted_actions(started, cfg, VOCAB,
                                    on_error=problems.append)
    assert actions == []
    assert any("no contracted effects" in p for p in problems)


def test_a_tool_the_server_does_not_offer_is_refused(servers):
    started, cfg = servers
    contract(cfg, {"deploy": {"effects": ["docs.published"]}})
    problems = []
    actions, _ = contracted_actions(started, cfg, VOCAB,
                                    on_error=problems.append)
    assert actions == []
    assert any("does not offer that tool" in p for p in problems)


def test_a_tool_may_raise_its_risk_but_never_lower_it(servers):
    """The server's risk is the operator's floor. A tool that could call
    itself safer than the server exposing it would route around the gate."""
    started, cfg = servers
    contract(cfg, {"publish": {"effects": ["docs.published"],
                               "risk": "irreversible"}})
    actions, _ = contracted_actions(started, cfg, VOCAB)
    assert actions[0].risk == "irreversible"

    contract(cfg, {"publish": {"effects": ["docs.published"], "risk": "read"}})
    actions, _ = contracted_actions(started, cfg, VOCAB)
    assert actions[0].risk == "network"


def test_the_operator_owns_the_arguments(servers):
    """The planner picks actions, not arguments. A model choosing arguments
    is a model deciding what happens, and nothing downstream is verifiable."""
    started, cfg = servers
    contract(cfg, {"publish": {"effects": ["docs.published"],
                               "arguments": {"path": "site/"}}})
    _, skills = contracted_actions(started, cfg, VOCAB)
    assert skills["mcp.docs:publish"].arguments == {"path": "site/"}


def test_a_tool_result_taints_the_session(servers, tmp_path):
    """It is words from somebody else's process, so everything after it is
    escalated. Recorded when the reply arrives, not one action later."""
    started, cfg = servers
    contract(cfg, {"publish": {"effects": ["docs.published"]}})
    provenance = Provenance()
    _, skills = contracted_actions(started, cfg, VOCAB,
                                   provenance=provenance)

    class Ctx:
        worktree = tmp_path

        class blobs:
            @staticmethod
            def put(text):
                return "sha"

        @staticmethod
        def say(text):
            pass

    assert provenance.sources == []
    result = skills["mcp.docs:publish"].run(Ctx())
    assert result.ok is True
    assert result.claimed_props == frozenset({"docs.published"})
    assert provenance.sources == ["mcp:mcp.docs:publish"]


def test_the_journal_explains_the_taint_on_its_own():
    """A receipt has to say where untrusted content came from without the
    reader having access to anything but the chain."""
    assert external_sources({"t": "EXEC", "action": "mcp.docs:publish"}) \
        == ["mcp:mcp.docs:publish"]
    # An ordinary action is not ingress, however networked it is.
    assert external_sources({"t": "EXEC", "action": "PushBranch"}) == []


@needs_git
def test_a_lying_tool_becomes_an_incident(repo, tmp_path, servers):
    """The stub reports success and changes nothing. The sensors disagree,
    and that disagreement is the whole product.

    No patch is queued on purpose, so the contracted tool is the ONLY route
    the planner has to a green suite and it is guaranteed to reach for it.
    With a working ApplyPatch available it would usually take that instead,
    and the test would pass by never exercising anything.
    """
    started, cfg = servers
    contract(cfg, {"publish": {"effects": ["tests.pass:unit"],
                               "arguments": {"path": "site/"}}})
    episode_dir = tmp_path / "mcp-ep"

    result, executor, journal_path = run_repo_episode(
        repo, episode_dir, frozenset({"tests.pass:unit"}), TEST_CMD,
        gate=PolicyGate("autonomous", approver=lambda *a: True),
        profile="autonomous", product_review=False, mcp_config=cfg)

    entries = Journal.read(str(journal_path))
    ran = [e for e in entries
           if e["t"] == "EXEC" and str(e.get("action", "")).startswith("mcp.")]
    assert ran, "the planner never reached the only action that claimed the goal"
    assert ran[0]["ok"] is True, "the stub reports success"

    incidents = [e for e in entries if e["t"] == "INCIDENT"]
    assert incidents, "a tool claiming an effect it did not produce must " \
                      "become an incident"
    assert "tests.pass:unit" in incidents[0]["claimed_not_observed"]

    # And it did not get away with it: the suite is still red, so the goal
    # was not reached on the strength of a tool's say-so.
    assert not result.success
