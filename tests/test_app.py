"""The desktop control surface.

The point of these is the approver. Everything else here is reading the
journal, which the receipt tests already cover, but `DesktopApprover` is the
thing that decides whether an unattended run can proceed, and it runs across
two threads. Its failure modes are a hang, a double answer, and a default of
yes, so those are what is tested.
"""

import json
import threading
import time
import urllib.request

import pytest
from conftest import GOAL, TEST_CMD, needs_git, queue_fix

from ledger import app
from ledger.executor import PolicyGate, run_repo_episode


# -- the approver ------------------------------------------------------------

def test_a_question_waits_until_the_window_answers():
    approver = app.DesktopApprover(timeout=5)
    result = {}

    def ask():
        result["approved"] = approver("PushBranch", "network")

    thread = threading.Thread(target=ask)
    thread.start()
    for _ in range(50):                      # let it register
        if approver.pending():
            break
        time.sleep(0.02)

    waiting = approver.pending()
    assert len(waiting) == 1
    assert waiting[0]["action"] == "PushBranch"
    assert waiting[0]["risk"] == "network"

    assert approver.resolve(waiting[0]["id"], True)
    thread.join(timeout=5)
    assert result["approved"] is True
    assert approver.pending() == []


def test_no_answer_is_a_denial():
    """Same reasoning as tty_approver with no terminal: a gate that defaults
    to yes when nobody is there is not a gate."""
    approver = app.DesktopApprover(timeout=0.2)
    assert approver("CreatePR", "irreversible") is False


def test_denial_travels_as_a_denial():
    approver = app.DesktopApprover(timeout=5)
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault("a", approver("Commit", "write")))
    thread.start()
    while not approver.pending():
        time.sleep(0.01)
    approver.resolve(approver.pending()[0]["id"], False)
    thread.join(timeout=5)
    assert result["a"] is False


def test_answering_twice_is_refused_not_crashed():
    approver = app.DesktopApprover(timeout=5)
    thread = threading.Thread(target=lambda: approver("Commit", "write"))
    thread.start()
    while not approver.pending():
        time.sleep(0.01)
    gate_id = approver.pending()[0]["id"]
    assert approver.resolve(gate_id, True)
    thread.join(timeout=5)
    # The window can double-click, or two windows can be open.
    assert approver.resolve(gate_id, True) is False
    assert approver.resolve("never-existed", True) is False


def test_every_decision_lands_in_history():
    approver = app.DesktopApprover(timeout=0.2)
    approver("RunSuite", "exec")
    assert len(approver.history) == 1
    assert approver.history[0]["approved"] is False
    assert approver.history[0]["action"] == "RunSuite"


# -- it is a real Gate -------------------------------------------------------

def test_policygate_accepts_it_unchanged():
    """It has to be a drop-in for tty_approver or the whole thing is a fork."""
    approver = app.DesktopApprover(timeout=5)
    gate = PolicyGate("attended", approver=approver)
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault("a", gate("ApplyPatch:a", "write")))
    thread.start()
    while not approver.pending():
        time.sleep(0.01)
    approver.resolve(approver.pending()[0]["id"], True)
    thread.join(timeout=5)
    assert result["a"] is True


def test_a_deny_rule_never_reaches_the_window():
    """Policy outranks consent, so a denied family must not even be asked.
    Otherwise the window teaches people their answer matters when it does
    not."""
    approver = app.DesktopApprover(timeout=0.2)
    gate = PolicyGate("attended", approver=approver,
                      rules={"deny": ["PushBranch"]})
    assert gate("PushBranch", "network") is False
    assert approver.history == []


# -- the HTTP surface --------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    srv, approver, url = app.serve(tmp_path, port=0)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield url, approver
    srv.shutdown()
    srv.server_close()


def get(url):
    """Returns the JSON body whatever the status: a 404 here carries an
    explanation, and swallowing it as an exception loses that."""
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode())


def post(url, payload):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_health_and_empty_episode_list(server):
    url, _ = server
    assert get(url + "/api/health")["ok"] is True
    assert get(url + "/api/episodes")["episodes"] == []


def test_a_gate_can_be_answered_over_http(server):
    url, approver = server
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault("a", approver("Commit", "write")))
    thread.start()
    while not approver.pending():
        time.sleep(0.01)

    listed = get(url + "/api/gates")["pending"]
    assert len(listed) == 1
    status, body = post(url + "/api/gate",
                        {"id": listed[0]["id"], "approved": True})
    assert status == 200 and body["approved"] is True
    thread.join(timeout=5)
    assert result["a"] is True


def test_answering_a_gate_that_is_gone_is_a_conflict_not_a_crash(server):
    url, _ = server
    status, _ = post(url + "/api/gate", {"id": "nope", "approved": True})
    assert status == 409


@needs_git
def test_it_reads_a_real_episode(repo, tmp_path):
    """The window is drawn from the journal, so an episode run entirely
    without it still shows up complete afterwards."""
    episode_dir = repo / ".ledger" / "episodes" / "demo"
    queue_fix(episode_dir)
    run_repo_episode(repo, episode_dir, GOAL, TEST_CMD,
                     gate=PolicyGate("autonomous", approver=lambda *a: True),
                     profile="autonomous", product_review=False)

    srv, _, _ = app.serve(repo, port=0)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        episodes = get(url + "/api/episodes")["episodes"]
        assert [e["name"] for e in episodes] == ["demo"]
        assert episodes[0]["success"] is True

        detail = get(url + "/api/episode/demo")
        kinds = {t["t"] for t in detail["timeline"]}
        assert {"exec", "end"} <= kinds
        assert get(url + "/api/receipt/demo")["v"].startswith("ledger.receipt/")
        assert get(url + "/api/episode/nope") == {"error": "no such episode"}
    finally:
        srv.shutdown()
        srv.server_close()


# -- across processes --------------------------------------------------------

def test_a_run_in_another_process_can_be_approved_in_the_window(server):
    """The workflow the desktop app exists for: window open here, run there."""
    url, approver = server
    remote = app.RemoteApprover(url, timeout=10, poll_every=0.05)
    result = {}

    thread = threading.Thread(
        target=lambda: result.setdefault("a", remote("PushBranch", "network")))
    thread.start()
    while not approver.pending():
        time.sleep(0.01)

    waiting = approver.pending()[0]
    assert waiting["action"] == "PushBranch"
    post(url + "/api/gate", {"id": waiting["id"], "approved": True})
    thread.join(timeout=10)
    assert result["a"] is True
    # Answered questions leave the window rather than sitting there with live
    # buttons on a gate that has already been decided.
    assert approver.pending() == []


def test_a_remote_denial_is_a_denial(server):
    url, approver = server
    remote = app.RemoteApprover(url, timeout=10, poll_every=0.05)
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault("a", remote("Commit", "write")))
    thread.start()
    while not approver.pending():
        time.sleep(0.01)
    post(url + "/api/gate", {"id": approver.pending()[0]["id"],
                             "approved": False})
    thread.join(timeout=10)
    assert result["a"] is False


def test_no_app_means_denied_not_crashed():
    """A closed window is not a human saying yes, and it must not be an
    exception in the middle of an episode either."""
    remote = app.RemoteApprover("http://127.0.0.1:1", timeout=1,
                                poll_every=0.05)
    assert remote("PushBranch", "network") is False


def test_an_app_that_dies_mid_question_denies(server):
    url, approver = server
    remote = app.RemoteApprover(url, timeout=10, poll_every=0.05)
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault("a", remote("Commit", "write")))
    thread.start()
    while not approver.pending():
        time.sleep(0.01)
    remote.url = "http://127.0.0.1:1"          # window closed
    thread.join(timeout=10)
    assert result["a"] is False


def test_a_forgotten_question_denies(server):
    """The app restarted, or the sweep collected it. Either way nobody
    answered, so it cannot come back approved."""
    url, approver = server
    remote = app.RemoteApprover(url, timeout=10, poll_every=0.05)
    gate_id = approver.ask_async("Commit", "write")
    approver._pending.pop(gate_id)             # simulate the loss
    assert approver.poll(gate_id) == {"state": "unknown"}


def test_reachable_reports_honestly(server):
    url, _ = server
    assert app.RemoteApprover.reachable(url) is True
    assert app.RemoteApprover.reachable("http://127.0.0.1:1") is False


def test_the_window_sweeps_a_question_whose_asker_gave_up():
    approver = app.DesktopApprover(timeout=0.1)
    gate_id = approver.ask_async("Commit", "write")
    assert len(approver.pending()) == 1
    approver._pending[gate_id].asked_at -= 3600
    assert approver.pending() == []            # no orphan button in the window
