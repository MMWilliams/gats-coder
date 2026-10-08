"""Signed receipts, and the taint rule that gives them something to say."""

import json
import sys

import pytest
from conftest import GOAL, TEST_CMD, needs_git, queue_fix
from test_mcp_danger import STUB

import ledger.cli as cli

from ledger import receipt, signing
from ledger.chat import Conversation, Turn
from ledger.executor import PolicyGate, run_repo_episode
from ledger.journal import Journal, canonical
from ledger.llm import FakeBackend
from ledger.taint import Provenance, external_sources

# RFC 8032 section 7.1 test vectors. The point of carrying them is that the
# signing code is checked against the specification and not against itself:
# a self-consistent but wrong curve would pass every round-trip test here
# and produce receipts no other implementation can verify.
RFC8032 = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e0652249015"
     "55fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69d"
     "a085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


@pytest.mark.parametrize("seed,public,msg,sig", RFC8032)
def test_rfc8032_vectors(seed, public, msg, sig):
    seed, msg = bytes.fromhex(seed), bytes.fromhex(msg)
    assert signing.public_key(seed).hex() == public
    assert signing.sign(seed, msg).hex() == sig
    assert signing.verify(bytes.fromhex(public), msg, bytes.fromhex(sig))


def test_verify_rejects_a_flipped_bit():
    seed = bytes(range(32))
    public = signing.public_key(seed)
    sig = bytearray(signing.sign(seed, b"the claims"))
    assert signing.verify(public, b"the claims", bytes(sig))
    assert not signing.verify(public, b"the claims!", bytes(sig))
    sig[0] ^= 1
    assert not signing.verify(public, b"the claims", bytes(sig))


@pytest.fixture
def key():
    """A fixed signing key: receipts have to be byte-stable across runs."""
    seed = bytes(range(32))
    public = signing.public_key(seed)
    return {"alg": signing.ALG, "seed": seed.hex(), "public": public.hex(),
            "key_id": signing.key_id(public)}


def test_key_is_created_once_and_kept_private(tmp_path, monkeypatch):
    monkeypatch.setattr(signing, "keys_dir", lambda: tmp_path / "keys")
    created = signing.load_key()
    assert signing.load_key() == created                # stable, not rotated
    assert created["key_id"] == signing.key_id(
        bytes.fromhex(created["public"]))
    # The seed signs; it never travels.
    assert created["seed"] not in json.dumps(
        receipt.sign({"v": receipt.SCHEMA}, created))


# --- a journal to attest -----------------------------------------------

def write_journal(path, *, research=False, network_after=True):
    """A miniature episode: gate, act, optionally read the web, act again."""
    with Journal(str(path)) as journal:
        journal.append("HEADER", version="0.0.0-test", task_id="t1",
                       task_text="fix the failing test",
                       initial=[], goal=["tests.pass:unit"], catalog=[],
                       catalog_hash="cat", config={}, profile="autonomous",
                       branch="ledger/t1", segment=1)
        journal.append("GATE", action="ApplyPatch:a", risk="write",
                       approved=True, profile="autonomous", reason=None,
                       tainted=[])
        journal.append("EXEC", action="ApplyPatch:a", ok=True, claims=[],
                       claimed_props=["patch.applied:a"], commands=[],
                       artifacts={}, risk="write")
        if research:
            journal.append("RESEARCH", query="how do I push",
                           sources=["https://example.test/docs"], chars=120)
        if network_after:
            journal.append("GATE", action="PushBranch", risk="network",
                           approved=True, profile="autonomous", reason=None,
                           tainted=(["https://example.test/docs"]
                                    if research else []))
            journal.append("EXEC", action="PushBranch", ok=True, claims=[],
                           claimed_props=[], risk="network",
                           commands=[{"cmd": "git push -u origin HEAD",
                                      "exit": 0, "out": "abc"}],
                           artifacts={})
        journal.append("END", success=True, steps=2,
                       plan=["ApplyPatch:a"], llm_planning_calls=0)
    return path


def test_receipt_round_trips_and_binds_to_the_journal(tmp_path, key):
    path = write_journal(tmp_path / "episode.jsonl", network_after=False)
    doc = receipt.issue(path, key)
    verdict = receipt.verify(doc, path)
    assert verdict.ok, verdict.problems
    assert doc["episode"]["task"] == "fix the failing test"
    assert doc["outcome"]["success"] is True
    assert doc["journal"]["segments"][0]["head"] \
        == Journal.read(str(path))[-1]["h"]

    # Signature alone still verifies without the journal in hand.
    assert receipt.verify(doc).ok


def test_altered_receipt_fails_its_own_signature(tmp_path, key):
    path = write_journal(tmp_path / "episode.jsonl", network_after=False)
    doc = receipt.issue(path, key)

    doc["totals"]["incidents"] = 0
    doc["incidents"] = []
    doc["decisions"][0]["approved"] = True
    doc["episode"]["task"] = "something harmless"
    verdict = receipt.verify(doc)
    assert not verdict.ok
    assert any("signature does not verify" in p for p in verdict.problems)


def test_a_receipt_cannot_survive_its_journal_being_edited(tmp_path, key):
    path = write_journal(tmp_path / "episode.jsonl", network_after=False)
    doc = receipt.issue(path, key)

    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["approved"] = False
    lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    verdict = receipt.verify(doc, path)
    assert not verdict.ok
    # The signature is still good -- it is the journal that moved.
    assert not any("signature does not verify" in p
                   for p in verdict.problems)
    assert any("does not match the journal" in p for p in verdict.problems)


# --- an episode is more than one journal file ---------------------------

def write_segment(path, *, profile, success, tainted=(), inherited=()):
    with Journal(str(path)) as journal:
        journal.append("HEADER", version="0.0.0-test", task_id="t1",
                       task_text="make the suite green", initial=[],
                       goal=["tests.pass:unit"], catalog=[], catalog_hash="c",
                       config={}, profile=profile, branch="ledger/t1",
                       inherited_taint=list(inherited))
        journal.append("GATE", action="ApplyPatch:a", risk="write",
                       approved=True, profile=profile, reason=None,
                       tainted=list(tainted) or list(inherited))
        journal.append("EXEC", action="ApplyPatch:a", ok=success, claims=[],
                       claimed_props=[], artifacts={}, commands=[],
                       risk="write")
        journal.append("END", success=success, steps=1,
                       plan=["ApplyPatch:a"], llm_planning_calls=0)
        if not success:
            journal.append("ESCALATION", reason="budget_exhausted",
                           denials=0, author_attempts=6,
                           author_attempt_limit=6)
    return path


def test_a_receipt_covers_every_segment_of_a_resumed_episode(tmp_path):
    """The attempt that failed and the attempt that worked are both in it.

    A receipt for episode.jsonl alone would be true and would omit the
    segment that did the work, which is the sort of honest document nobody
    should accept.
    """
    write_segment(tmp_path / "episode.jsonl", profile="autonomous",
                  success=False)
    write_segment(tmp_path / "episode.r2.jsonl", profile="attended",
                  success=True)

    doc = receipt.extract(tmp_path)
    assert [s["file"] for s in doc["journal"]["segments"]] \
        == ["episode.jsonl", "episode.r2.jsonl"]
    assert doc["totals"]["runs"] == 2
    assert [r["profile"] for r in doc["runs"]] == ["autonomous", "attended"]
    assert [r["escalation"] for r in doc["runs"]] == ["budget_exhausted", None]
    # The outcome is where the task ended up; the totals are the whole cost.
    assert doc["outcome"]["success"] is True
    assert doc["totals"]["steps"] == 2
    assert len(doc["decisions"]) == 2
    assert [d["run"] for d in doc["decisions"]] == [0, 1]
    # Both profiles that were actually in play are explained.
    assert sorted(doc["policy"]["gated_risks"]) == ["attended", "autonomous"]

    # Naming any segment, or the directory, attests the same episode.
    assert canonical(receipt.extract(tmp_path / "episode.r2.jsonl")) \
        == canonical(doc)


def test_run_scope_attests_one_file_and_says_so(tmp_path):
    write_segment(tmp_path / "episode.jsonl", profile="autonomous",
                  success=False)
    write_segment(tmp_path / "episode.r2.jsonl", profile="attended",
                  success=True)

    doc = receipt.extract(tmp_path / "episode.jsonl", scope="run")
    assert doc["scope"] == "run"
    assert doc["totals"]["runs"] == 1
    assert [s["file"] for s in doc["journal"]["segments"]] == ["episode.jsonl"]
    assert doc["outcome"]["success"] is False


def test_a_re_run_appended_to_one_file_is_still_two_runs(tmp_path):
    """Resume writes a new file; a re-run appends another HEADER to this
    one. Both are runs and both belong in the receipt."""
    path = tmp_path / "episode.jsonl"
    write_segment(path, profile="attended", success=False)
    write_segment(path, profile="attended", success=True)

    doc = receipt.extract(path)
    assert len(doc["journal"]["segments"]) == 1
    assert doc["journal"]["segments"][0]["runs"] == 2
    assert doc["totals"]["runs"] == 2
    assert doc["outcome"]["success"] is True


def test_a_receipt_stays_verifiable_after_the_episode_continues(
        tmp_path, key):
    write_segment(tmp_path / "episode.jsonl", profile="autonomous",
                  success=False)
    doc = receipt.issue(tmp_path, key)
    assert receipt.verify(doc, tmp_path).ok

    write_segment(tmp_path / "episode.r2.jsonl", profile="attended",
                  success=True)
    verdict = receipt.verify(doc, tmp_path)
    # Still true about what it attests -- a document that expires because
    # work continued is a document nobody would rely on.
    assert verdict.ok, verdict.problems
    assert any("the episode continued" in n for n in verdict.notes)


def test_growth_inside_an_attested_segment_is_a_note_not_a_failure(
        tmp_path, key):
    path = tmp_path / "episode.jsonl"
    write_segment(path, profile="attended", success=True)
    doc = receipt.issue(tmp_path, key)

    write_segment(path, profile="attended", success=True)  # a second run
    verdict = receipt.verify(doc, tmp_path)
    assert verdict.ok, verdict.problems
    assert any("has grown by" in n for n in verdict.notes)


def test_a_missing_segment_fails_verification(tmp_path, key):
    write_segment(tmp_path / "episode.jsonl", profile="attended",
                  success=False)
    write_segment(tmp_path / "episode.r2.jsonl", profile="attended",
                  success=True)
    doc = receipt.issue(tmp_path, key)

    (tmp_path / "episode.r2.jsonl").unlink()
    verdict = receipt.verify(doc, tmp_path)
    assert not verdict.ok
    assert any("missing" in p for p in verdict.problems)


def test_taint_does_not_leak_across_a_resume(tmp_path):
    """A resumed episode is a new process with a new context. What carries
    is what the HEADER says it inherited -- nothing else."""
    write_segment(tmp_path / "episode.jsonl", profile="autonomous",
                  success=False, tainted=["https://example.test/docs"])
    write_segment(tmp_path / "episode.r2.jsonl", profile="autonomous",
                  success=True)

    doc = receipt.extract(tmp_path)
    assert doc["runs"][0]["first_external_at"] is not None
    assert doc["runs"][1]["first_external_at"] is None
    # The union still records what the task ever touched...
    assert doc["provenance"]["external_sources"] \
        == ["https://example.test/docs"]
    # ...but the second run's action is not reported as exposed, and the
    # clean gate in it is not a gap.
    assert [a["run"] for a in doc["provenance"]["privileged_after_external"]] \
        == [0]
    assert doc["provenance"]["gaps"] == []


# --- provenance ---------------------------------------------------------

def test_external_sources_only_counts_ingress():
    assert external_sources({"t": "RESEARCH", "sources": ["https://a"]}) \
        == ["https://a"]
    assert external_sources({"t": "MCP_RESULT", "tool": "fetch"}) \
        == ["mcp:fetch"]
    assert external_sources({"t": "MCP_RESULT", "tool": "fetch",
                             "denied": True}) == []
    # Sending is not reading: PushBranch is gated on risk, not on taint.
    assert external_sources({"t": "EXEC", "action": "PushBranch",
                             "risk": "network"}) == []


def test_receipt_reports_privileged_actions_taken_after_untrusted_content(
        tmp_path):
    clean = receipt.extract(write_journal(tmp_path / "clean.jsonl",
                                          research=False))
    assert clean["provenance"]["external_sources"] == []
    assert clean["provenance"]["privileged_after_external"] == []
    assert clean["provenance"]["gaps"] == []

    dirty = receipt.extract(write_journal(tmp_path / "dirty.jsonl",
                                          research=True))
    prov = dirty["provenance"]
    assert prov["external_sources"] == ["https://example.test/docs"]
    assert [a["action"] for a in prov["privileged_after_external"]] \
        == ["PushBranch"]
    assert prov["gaps"] == []


def test_a_gate_that_missed_an_ingress_is_reported_as_a_gap(tmp_path, key):
    """The live gate and the journal disagree -> the receipt says so.

    This is the failure that matters: the escalation rule did not fire
    because the gate never learned about a source the journal records.
    """
    path = tmp_path / "gap.jsonl"
    with Journal(str(path)) as journal:
        journal.append("HEADER", version="0.0.0-test", task_id="t1",
                       initial=[], goal=[], catalog=[], catalog_hash="c",
                       config={}, profile="autonomous")
        journal.append("MCP_RESULT", tool="fetch", chars=900, denied=False)
        journal.append("GATE", action="PushBranch", risk="network",
                       approved=True, profile="autonomous", reason=None,
                       tainted=[])
        journal.append("END", success=True, steps=1, plan=[])

    doc = receipt.extract(path)
    assert doc["provenance"]["gaps"]
    assert "mcp:fetch" in doc["provenance"]["external_sources"]
    verdict = receipt.verify(receipt.sign(doc, key), path)
    assert not verdict.ok
    assert any("provenance gap" in p for p in verdict.problems)


def test_command_lines_are_redacted_in_the_receipt(tmp_path):
    path = tmp_path / "secret.jsonl"
    with Journal(str(path)) as journal:
        journal.append("HEADER", version="0.0.0-test", task_id="t", initial=[],
                       goal=[], catalog=[], catalog_hash="c", config={},
                       profile="attended")
        journal.append("EXEC", action="Curl", ok=True, claims=[],
                       claimed_props=[], artifacts={}, risk="network",
                       commands=[{"cmd": "curl -H 'Authorization: Bearer "
                                         "sk-ant-api03-REALKEYMATERIAL'",
                                  "exit": 0, "out": "sha"}])
        journal.append("END", success=True, steps=1, plan=[])

    text = json.dumps(receipt.extract(path))
    assert "REALKEYMATERIAL" not in text


# --- the gate -----------------------------------------------------------

def approver_that_counts(calls):
    def approve(action_name, risk):
        calls.append((action_name, risk))
        return True
    return approve


def test_taint_escalates_past_the_autonomy_profile():
    prov = Provenance()
    calls = []
    gate = PolicyGate(profile="autonomous",
                      approver=approver_that_counts(calls),
                      taint_probe=prov)

    assert gate("ApplyPatch:a", "write") is True
    assert calls == []                       # autonomous does not stop on write

    prov.note("https://example.test/docs")
    assert gate("ApplyPatch:b", "write") is True
    assert calls == [("ApplyPatch:b", "write")]
    assert "untrusted content in scope" in gate.last_reason

    # Reads are still free: the rule is about acting, not about looking.
    assert gate("RunSuite", "read") is True
    assert len(calls) == 1


def test_an_approval_given_before_taint_is_not_reused_after_it():
    prov = Provenance()
    calls = []
    gate = PolicyGate(profile="supervised",
                      approver=approver_that_counts(calls),
                      taint_probe=prov)

    assert gate("PushBranch", "network") is True
    assert len(calls) == 1
    assert gate("PushBranch", "network") is True
    assert len(calls) == 1                   # memoised within the episode

    prov.note("mcp:fetch")
    assert gate("PushBranch", "network") is True
    assert len(calls) == 2                   # asked again, on the new footing


def test_a_deny_rule_still_outranks_everything():
    prov = Provenance(["https://example.test"])
    gate = PolicyGate(profile="attended", approver=lambda *a: True,
                      rules={"deny": ["PushBranch"]}, taint_probe=prov)
    assert gate("PushBranch", "network") is False


def test_an_explicit_allow_rule_survives_taint():
    """Policy is the operator saying so in advance, which is the one thing
    a fetched page cannot do."""
    prov = Provenance(["https://example.test"])
    calls = []
    gate = PolicyGate(profile="autonomous",
                      approver=approver_that_counts(calls),
                      rules={"allow": ["ApplyPatch"]}, taint_probe=prov)
    assert gate("ApplyPatch:a", "write") is True
    assert calls == []


def test_without_a_probe_the_gate_behaves_exactly_as_before():
    calls = []
    gate = PolicyGate(profile="autonomous",
                      approver=approver_that_counts(calls))
    assert gate("ApplyPatch:a", "write") is True
    assert gate("PushBranch", "network") is True
    assert calls == []


def test_sources_the_gate_saw_but_no_ingress_explains_are_declared(tmp_path):
    """The gate escalated on something the journal cannot account for.

    Not a safety hole -- it was stricter than the record explains -- but a
    receipt that showed a clean sheet here would be lying by omission.
    """
    path = tmp_path / "seeded.jsonl"
    with Journal(str(path)) as journal:
        journal.append("HEADER", version="0.0.0-test", task_id="t", initial=[],
                       goal=[], catalog=[], catalog_hash="c", config={},
                       profile="autonomous")
        journal.append("GATE", action="Commit", risk="write", approved=True,
                       profile="autonomous",
                       reason="untrusted content in scope: mcp:inbox",
                       tainted=["mcp:inbox"])
        journal.append("EXEC", action="Commit", ok=True, claims=[],
                       claimed_props=[], artifacts={}, commands=[],
                       risk="write")
        journal.append("END", success=True, steps=1, plan=["Commit"])

    prov = receipt.extract(path)["provenance"]
    assert prov["external_sources"] == ["mcp:inbox"]
    assert prov["unexplained_sources"] == ["mcp:inbox"]
    assert prov["gaps"] == []
    assert [a["action"] for a in prov["privileged_after_external"]] \
        == ["Commit"]


def test_a_tool_result_taints_the_conversation_but_a_refusal_does_not():
    """The conversation is where untrusted content enters; what it
    constrains is what the planner is later allowed to do."""
    replies = iter([
        json.dumps({"tool": "docs:fetch", "arguments": {"url": "..."}}),
        json.dumps({"tool": "docs:fetch", "arguments": {"url": "..."}}),
        "here is what those say",
    ])
    results = iter([
        "DENIED by the permission gate -- tell the user and continue "
        "without it",
        "TOOL ERROR: upstream said no",
    ])
    prov = Provenance()
    conv = Conversation(backend=FakeBackend(lambda _: next(replies)),
                        tools={"docs:fetch": "fetch a page"},
                        tool_call=lambda name, args: next(results),
                        provenance=prov)

    conv.ask("what do the vendor docs say?")
    # First call was refused: our own string, nothing from the server.
    # Second returned an error the server wrote, which is still its words.
    assert prov.sources == ["mcp:docs:fetch"]


@needs_git
def test_the_chat_session_shares_one_provenance_with_its_episodes(
        repo, monkeypatch):
    """The wiring, end to end through the CLI.

    Two provenance objects instead of one is invisible from either side --
    the gate escalates correctly and the receipt still reports a clean
    sheet -- so the thing worth asserting is object identity.
    """
    stub = repo / "mcp_stub.py"
    stub.write_text(STUB, encoding="utf-8")
    config = repo / ".ledger" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"mcp": {"servers": {"docs": {
        "command": [sys.executable, str(stub)], "risk": "network"}}}}),
        encoding="utf-8")

    replies = iter([json.dumps({"tool": "docs:echo",
                                "arguments": {"text": "hi"}}),
                    "the docs say hi"])
    monkeypatch.setattr(cli, "_backend",
                        lambda *a, **k: FakeBackend(lambda _: next(replies)))

    seen, gates = {}, []
    real_runner, real_gate = cli._chat_runner, cli._gate_and_profile

    def capture_runner(root, args, cfg_file, backend, provenance=None):
        seen["provenance"] = provenance
        return real_runner(root, args, cfg_file, backend, provenance)

    def capture_gate(args, cfg, taint_probe=None):
        gate, profile = real_gate(args, cfg, taint_probe)
        gates.append(gate)
        return gate, profile

    monkeypatch.setattr(cli, "_chat_runner", capture_runner)
    monkeypatch.setattr(cli, "_gate_and_profile", capture_gate)

    args = cli.build_parser().parse_args(
        ["chat", "--repo", str(repo), "--profile", "autonomous",
         "--ask", "what do the docs say?"])
    assert cli.cmd_chat(args) == 0

    provenance = seen["provenance"]
    assert provenance.sources == ["mcp:docs:echo"]
    assert gates, "the MCP tool gate was never built"
    assert all(g.taint_probe is provenance for g in gates)


@needs_git
def test_a_tainted_session_hands_its_taint_down_to_the_episode(repo, tmp_path):
    """Read an MCP result in chat, then ask for a change: the episode runs
    escalated, and its own receipt can say why without the conversation."""
    episode_dir = tmp_path / "ep"
    queue_fix(episode_dir)
    prov = Provenance()
    conv = Conversation(backend=FakeBackend("done"),
                        tool_call=lambda name, args: "ticket text",
                        tools={"tickets:read": "read a ticket"},
                        provenance=prov)
    conv.turns.append(Turn("assistant", "[tool] tickets:read {}"))
    prov.note("mcp:tickets:read")            # what conv.ask records

    asked = []
    gate = PolicyGate(profile="autonomous",
                      approver=lambda a, r: asked.append((a, r)) or True,
                      taint_probe=prov)
    result, _, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD, gate=gate, profile="autonomous",
        product_review=False)

    assert result.success, result.plan
    assert asked, "an episode launched from a tainted session must escalate"

    header = Journal.read(str(journal_path))[0]
    assert header["inherited_taint"] == ["mcp:tickets:read"]

    doc = receipt.extract(journal_path)
    assert doc["provenance"]["external_sources"] == ["mcp:tickets:read"]
    assert doc["runs"][0]["inherited_taint"] == ["mcp:tickets:read"]
    assert doc["runs"][0]["first_external_at"] == 0    # the HEADER itself
    # Explained by this journal alone: a reader needs no access to the chat.
    assert doc["provenance"]["unexplained_sources"] == []
    assert doc["provenance"]["gaps"] == []


@needs_git
def test_the_journal_records_the_provenance_the_gate_actually_consulted(
        repo, tmp_path):
    """A caller-supplied gate keeps its own probe; the executor must journal
    THAT one. Two provenance objects and the receipt reports a different
    episode than the gate decided on -- invisible to every unit test here,
    which is why this one runs a real episode.
    """
    episode_dir = tmp_path / "ep"
    queue_fix(episode_dir)
    prov = Provenance(["https://example.test/docs"])
    asked = []

    def approver(action, risk):
        asked.append((action, risk))
        return True

    gate = PolicyGate(profile="autonomous", approver=approver,
                      taint_probe=prov)
    result, _, journal_path = run_repo_episode(
        repo, episode_dir, GOAL, TEST_CMD, gate=gate, profile="autonomous",
        product_review=False)

    assert result.success, result.plan
    assert asked, "autonomous + taint must still stop on non-read actions"

    prov_block = receipt.extract(journal_path)["provenance"]
    assert prov_block["external_sources"] == ["https://example.test/docs"]
    assert prov_block["gaps"] == []
    assert [a["action"] for a in prov_block["privileged_after_external"]] \
        == result.plan


# --- which model wrote it ---------------------------------------------------

def test_the_receipt_names_the_model_that_authored_each_patch(tmp_path):
    """"Bring your own model" and "here is what happened" are the same
    product. An audit that cannot say which model wrote a line of shipped
    code has answered half the question."""
    path = tmp_path / "episode.jsonl"
    with Journal(str(path)) as journal:
        journal.append("HEADER", version="0.0.0-test", task_id="t",
                       task_text="fix it", initial=[], goal=["tests.pass:unit"],
                       catalog=[], catalog_hash="c", config={},
                       profile="autonomous", goal_backend="anthropic:opus")
        journal.append("EXEC", action="RunSuite", ok=False, claims=[],
                       claimed_props=[], artifacts={}, commands=[], risk="exec")
        journal.append("EXEC", action="AuthorPatch:a", ok=True, claims=[],
                       claimed_props=["patch.queued:a"], artifacts={},
                       commands=[], risk="write", model="anthropic:opus")
        journal.append("EXEC", action="ApplyPatch:a", ok=True, claims=[],
                       claimed_props=[], artifacts={}, commands=[], risk="write")
        journal.append("END", success=True, steps=3, plan=["AuthorPatch:a"])

    doc = receipt.extract(path)
    by_action = {a["action"]: a for a in doc["actions"]}
    assert by_action["AuthorPatch:a"]["model"] == "anthropic:opus"
    # Actions that called no model say so rather than inheriting one.
    assert by_action["RunSuite"]["model"] is None
    assert by_action["ApplyPatch:a"]["model"] is None

    assert doc["runs"][0]["goal_backend"] == "anthropic:opus"
    roster = {m["model"]: m["used_for"] for m in doc["models"]}
    assert "anthropic:opus" in roster
    assert "goal compilation" in roster["anthropic:opus"]
    assert "AuthorPatch" in roster["anthropic:opus"]


def test_two_models_across_a_resume_are_both_named(tmp_path):
    """The reason the roster is first-seen order rather than sorted: on a
    resumed episode the model can change between attempts, and which one
    wrote what is the question being asked."""
    path = tmp_path / "episode.jsonl"
    with Journal(str(path)) as journal:
        journal.append("HEADER", version="0.0.0-test", task_id="t", initial=[],
                       goal=[], catalog=[], catalog_hash="c", config={},
                       profile="autonomous")
        journal.append("EXEC", action="AuthorPatch:a", ok=True, claims=[],
                       claimed_props=[], artifacts={}, commands=[],
                       risk="write", model="ollama:qwen3-coder")
        journal.append("END", success=False, steps=1, plan=[])
    with Journal(str(path)) as journal:
        journal.append("HEADER", version="0.0.0-test", task_id="t", initial=[],
                       goal=[], catalog=[], catalog_hash="c", config={},
                       profile="autonomous")
        journal.append("EXEC", action="AuthorPatch:b", ok=True, claims=[],
                       claimed_props=[], artifacts={}, commands=[],
                       risk="write", model="anthropic:opus")
        journal.append("END", success=True, steps=1, plan=[])

    models = [m["model"] for m in receipt.extract(path)["models"]]
    assert models == ["ollama:qwen3-coder", "anthropic:opus"]
