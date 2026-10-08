"""The local control surface the desktop app talks to.

`ledger app` starts this; the Tauri shell is a window pointed at it. Keeping
the logic here rather than in the shell means the same surface serves the
desktop app, a browser, and anything else, and it keeps LEDGER's promise of
no dependencies -- this is `http.server` and nothing else.

**It exists to close a real hole, not to be a nicer CLI.** `tty_approver`
denies when there is no terminal, on the correct reasoning that without a
terminal there is no human to ask. The consequence is that an episode
launched from anywhere except a shell cannot be approved at all: every gated
action is refused and the run escalates `gate_denied`. That is the single
thing standing between LEDGER and somebody who does not live in a terminal.

`DesktopApprover` is the answer. It is a gate approver like the others, so it
plugs into `PolicyGate(approver=...)` unchanged, but instead of reading stdin
it publishes the question and blocks until the window answers or the wait
runs out. Timing out DENIES, for the same reason the TTY one does: an
unanswered question is not a yes.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import AGENT_VERSION, receipt
from .journal import Journal, current_run

# How long a gate waits for a human in the window before giving up. Long
# enough to walk back to the desk, short enough that a forgotten run does not
# hold a worktree open all night.
GATE_TIMEOUT_S = 600


@dataclass
class Pending:
    """One question waiting on a person."""
    id: str
    action: str
    risk: str
    reason: str | None
    asked_at: float
    answer: queue.Queue = field(default_factory=queue.Queue, repr=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "action": self.action, "risk": self.risk,
                "reason": self.reason, "asked_at": self.asked_at,
                "waited_s": round(time.time() - self.asked_at, 1)}


class DesktopApprover:
    """A gate approver that asks a window instead of a terminal.

    Shared between the executor thread (which blocks in `__call__`) and the
    HTTP threads (which resolve it), so every mutation is under the lock.
    """

    def __init__(self, timeout: float = GATE_TIMEOUT_S):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._pending: dict[str, Pending] = {}
        self._answered: dict[str, bool] = {}
        self.history: list[dict] = []

    def _open(self, action_name: str, risk: str) -> Pending:
        question = Pending(id=uuid.uuid4().hex[:12], action=action_name,
                           risk=risk, reason=None, asked_at=time.time())
        with self._lock:
            self._pending[question.id] = question
        return question

    # -- the Gate protocol ---------------------------------------------------

    def __call__(self, action_name: str, risk: str) -> bool:
        """In-process: the episode thread blocks here until the window answers."""
        question = self._open(action_name, risk)
        try:
            return question.answer.get(timeout=self.timeout)
        except queue.Empty:
            # Nobody answered. A gate that defaults to yes when unattended is
            # not a gate, so this is the same answer tty_approver gives when
            # there is no terminal. `resolve` never ran, so the record of the
            # decision has to be written here instead.
            with self._lock:
                self._pending.pop(question.id, None)
            self.history.append({**question.to_dict(), "approved": False})
            return False

    # -- across processes ----------------------------------------------------
    #
    # `ledger app` in one window and `ledger run` in another is the workflow
    # the desktop app implies, and it means the asking process is not the
    # process holding this object. These two let a run raise a question over
    # HTTP and collect the answer later, without a second approver
    # implementation that could drift from this one.

    def ask_async(self, action_name: str, risk: str) -> str:
        return self._open(action_name, risk).id

    def poll(self, gate_id: str) -> dict:
        """`waiting`, `approved`, or `unknown` once it has been collected."""
        with self._lock:
            if gate_id in self._answered:
                return {"state": "answered",
                        "approved": self._answered.pop(gate_id)}
            question = self._pending.get(gate_id)
        if question is None:
            return {"state": "unknown"}
        return {"state": "waiting",
                "waited_s": round(time.time() - question.asked_at, 1)}

    # -- what the window sees ------------------------------------------------

    def pending(self) -> list[dict]:
        """Also sweeps questions whose asker has certainly given up, so a
        crashed run does not leave a button in the window forever."""
        cutoff = time.time() - (self.timeout + 60)
        with self._lock:
            stale = [i for i, p in self._pending.items() if p.asked_at < cutoff]
            for gate_id in stale:
                self._pending.pop(gate_id, None)
            return [p.to_dict() for p in self._pending.values()]

    def resolve(self, gate_id: str, approved: bool) -> bool:
        """The one place a decision becomes final.

        Popping from `_pending` here rather than in the waiter is what stops
        an answered question sitting in the window with live buttons, and it
        is also what makes a second click a clean no-op instead of a second
        answer to the same gate.
        """
        approved = bool(approved)
        with self._lock:
            question = self._pending.pop(gate_id, None)
            if question is None:
                return False
            # For a caller that polls rather than blocks. The queue put wakes
            # an in-process waiter; only one of the two is ever read, and
            # which one depends on how the question arrived.
            self._answered[gate_id] = approved
        self.history.append({**question.to_dict(), "approved": approved})
        question.answer.put(approved)
        return True


class RemoteApprover:
    """A gate approver that asks an app running in another process.

    `ledger app` in one window and `ledger run` in another is the workflow the
    desktop app implies, and it is the one that makes the app worth having:
    the window stays open, runs come and go. This posts the question to that
    window and polls for the answer.

    Every failure resolves to a denial. An app that has been closed, a network
    that will not connect, a question the server has forgotten: none of those
    are a human saying yes, and the whole value of the gate is that only a
    human saying yes counts as one.
    """

    def __init__(self, url: str, timeout: float = GATE_TIMEOUT_S,
                 poll_every: float = 0.75):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.poll_every = poll_every

    @staticmethod
    def reachable(url: str, timeout: float = 1.0) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/api/health",
                                        timeout=timeout) as response:
                return json.loads(response.read().decode()).get("ok") is True
        except Exception:                              # noqa: BLE001
            return False

    def _post(self, path: str, payload: dict) -> dict:
        import urllib.request
        request = urllib.request.Request(
            self.url + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode())

    def _get(self, path: str) -> dict:
        import urllib.request
        with urllib.request.urlopen(self.url + path, timeout=10) as response:
            return json.loads(response.read().decode())

    def __call__(self, action_name: str, risk: str) -> bool:
        try:
            gate_id = self._post("/api/ask", {"action": action_name,
                                              "risk": risk})["id"]
        except Exception:                              # noqa: BLE001
            return False

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                status = self._get("/api/ask/" + gate_id)
            except Exception:                          # noqa: BLE001
                return False                           # the window went away
            state = status.get("state")
            if state == "answered":
                return bool(status.get("approved"))
            if state == "unknown":
                # Swept, or the app restarted. Not an answer.
                return False
            time.sleep(self.poll_every)
        return False


# ---------------------------------------------------------------------------
# Reading episodes
# ---------------------------------------------------------------------------

def episode_dirs(root: Path) -> list[Path]:
    base = root / ".ledger" / "episodes"
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def episode_summary(directory: Path) -> dict:
    """Enough for a list row, cheap enough to do for every episode."""
    segments = sorted(directory.glob("episode*.jsonl"))
    if not segments:
        return {"name": directory.name, "runs": 0, "status": "empty"}
    entries = Journal.read(str(segments[-1]))
    run = current_run(entries)
    header = run[0] if run and run[0].get("t") == "HEADER" else {}
    end = next((e for e in reversed(run) if e.get("t") == "END"), {})
    escalation = next((e for e in reversed(run)
                       if e.get("t") == "ESCALATION"), None)
    # A journal with no END either belongs to a run happening right now or to
    # one whose process was killed. Nothing in the file distinguishes them, so
    # the only honest signal is how long ago it was last written: a live
    # episode touches its journal every step. Calling a months-dead run
    # "running" is worse than saying nothing, because it is the one word that
    # tells someone to wait.
    last_write = segments[-1].stat().st_mtime
    stale_after = 120
    return {
        "name": directory.name,
        "task": header.get("task_text") or header.get("task_id"),
        "profile": header.get("profile"),
        "segments": len(segments),
        "steps": end.get("steps", 0),
        "success": bool(end.get("success")),
        "escalation": escalation.get("reason") if escalation else None,
        "running": not end and (time.time() - last_write) < stale_after,
        "abandoned": not end and (time.time() - last_write) >= stale_after,
        "modified": last_write,
    }


def episode_detail(directory: Path) -> dict:
    """The run as the window draws it: the plan, what was gated, what ran.

    Deliberately built on the journal rather than on live objects. The
    journal is the thing that is true after the process exits, so the window
    shows the same account the receipt does.
    """
    segments = sorted(directory.glob("episode*.jsonl"))
    if not segments:
        return {"name": directory.name, "entries": []}
    entries = Journal.read(str(segments[-1]))
    run = current_run(entries)
    timeline = []
    for entry in run:
        kind = entry.get("t")
        if kind == "PLAN":
            timeline.append({"t": "plan", "i": entry.get("i"),
                             "step": entry.get("step"),
                             "chosen": entry.get("chosen")})
        elif kind == "GATE":
            timeline.append({"t": "gate", "i": entry.get("i"),
                             "action": entry.get("action"),
                             "risk": entry.get("risk"),
                             "approved": bool(entry.get("approved")),
                             "reason": entry.get("reason"),
                             "tainted": entry.get("tainted") or []})
        elif kind == "EXEC":
            timeline.append({"t": "exec", "i": entry.get("i"),
                             "action": entry.get("action"),
                             "risk": entry.get("risk"),
                             "ok": bool(entry.get("ok"))})
        elif kind == "INCIDENT":
            timeline.append({"t": "incident", "i": entry.get("i"),
                             "action": entry.get("action"),
                             "claimed": entry.get("claimed_not_observed") or []})
        elif kind == "ESCALATION":
            timeline.append({"t": "escalation", "i": entry.get("i"),
                             "reason": entry.get("reason")})
        elif kind == "END":
            timeline.append({"t": "end", "i": entry.get("i"),
                             "success": bool(entry.get("success")),
                             "steps": entry.get("steps", 0),
                             "plan": entry.get("plan") or []})
    return {**episode_summary(directory), "timeline": timeline,
            "segment": segments[-1].name}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class State:
    """What the handler is allowed to touch. One instance per server."""

    def __init__(self, root: Path, approver: DesktopApprover):
        self.root = root
        self.approver = approver
        self.started = time.time()


class Handler(BaseHTTPRequestHandler):
    state: State = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass                       # the window is the log

    def _send(self, code, body: bytes, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Loopback only, but the Tauri shell is a different origin.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload).encode("utf-8"),
                   "application/json")

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        root = self.state.root

        if path == "/":
            return self._send(200, UI.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/health":
            return self._json(200, {"ok": True, "version": AGENT_VERSION,
                                    "root": str(root),
                                    "uptime_s": round(time.time()
                                                      - self.state.started, 1)})
        if path == "/api/gates":
            return self._json(200, {"pending": self.state.approver.pending(),
                                    "history": self.state.approver.history[-50:]})
        if path == "/api/episodes":
            return self._json(200, {"episodes": [episode_summary(d)
                                                 for d in episode_dirs(root)]})
        if path.startswith("/api/episode/"):
            name = path[len("/api/episode/"):]
            directory = root / ".ledger" / "episodes" / name
            if not directory.is_dir():
                return self._json(404, {"error": "no such episode"})
            return self._json(200, episode_detail(directory))
        if path.startswith("/api/ask/"):
            return self._json(200,
                              self.state.approver.poll(path[len("/api/ask/"):]))
        if path.startswith("/api/receipt/"):
            name = path[len("/api/receipt/"):]
            directory = root / ".ledger" / "episodes" / name
            if not directory.is_dir():
                return self._json(404, {"error": "no such episode"})
            try:
                return self._json(200, receipt.issue(directory))
            except (OSError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "body must be JSON"})

        if path == "/api/ask":
            action = str(payload.get("action") or "").strip()
            risk = str(payload.get("risk") or "").strip()
            if not action or not risk:
                return self._json(400, {"error": "action and risk are required"})
            return self._json(200, {"id": self.state.approver.ask_async(action,
                                                                       risk)})
        if path == "/api/gate":
            gate_id = str(payload.get("id") or "")
            approved = bool(payload.get("approved"))
            if self.state.approver.resolve(gate_id, approved):
                return self._json(200, {"ok": True, "id": gate_id,
                                        "approved": approved})
            # Already answered, already timed out, or never existed. All three
            # are the same to the caller and none is a server error.
            return self._json(409, {"error": "no gate is waiting on that id"})
        self._json(404, {"error": "not found"})


UI = """<!doctype html>
<meta charset="utf-8"><title>LEDGER</title>
<style>
 :root{--bg:#0d0e10;--fg:#e8e8e4;--dim:#8a8a84;--line:#26272b;--ok:#5fb87a;
       --bad:#d66;--warn:#e0a83c}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:14px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}
 header{padding:14px 20px;border-bottom:1px solid var(--line);font-weight:700}
 main{display:grid;grid-template-columns:300px 1fr;height:calc(100vh - 49px)}
 aside{border-right:1px solid var(--line);overflow:auto}
 section{overflow:auto;padding:20px}
 .ep{padding:12px 16px;border-bottom:1px solid var(--line);cursor:pointer}
 .ep:hover{background:#16171a}
 .ep b{display:block;font-weight:600}
 .ep span{color:var(--dim);font-size:12px}
 .gate{margin:0 0 14px;padding:16px;border:1px solid var(--warn);
       border-radius:10px;background:#1b1710}
 .gate button{font:inherit;padding:8px 16px;margin-right:8px;cursor:pointer;
              border-radius:6px;border:1px solid var(--line);background:#222;
              color:var(--fg)}
 .gate button.yes{background:#1e3f2a;border-color:var(--ok)}
 .row{padding:6px 0;border-bottom:1px solid var(--line)}
 .dim{color:var(--dim)}
</style>
<header>LEDGER</header>
<main>
  <aside id="list"></aside>
  <section><div id="gates"></div><div id="detail" class="dim">
    Pick an episode.</div></section>
</main>
<script>
const j = (u,o) => fetch(u,o).then(r => r.json());
let current = null;

async function gates() {
  const {pending} = await j('/api/gates');
  document.getElementById('gates').innerHTML = pending.map(g => `
    <div class="gate">
      <div><b>${g.action}</b> <span class="dim">risk: ${g.risk}</span></div>
      ${g.reason ? `<div class="dim">${g.reason}</div>` : ''}
      <div class="dim">waiting ${g.waited_s}s</div>
      <div style="margin-top:10px">
        <button class="yes" onclick="answer('${g.id}',true)">Allow</button>
        <button onclick="answer('${g.id}',false)">Deny</button>
      </div>
    </div>`).join('');
}
async function answer(id, approved) {
  await j('/api/gate', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id, approved})});
  gates();
}
async function episodes() {
  const {episodes} = await j('/api/episodes');
  document.getElementById('list').innerHTML = episodes.map(e => `
    <div class="ep" onclick="open_('${e.name}')">
      <b>${e.task || e.name}</b>
      <span>${e.running ? 'running' : e.success ? 'reached the goal'
             : e.abandoned ? 'stopped without finishing'
             : (e.escalation || 'did not finish')} - ${e.steps} steps</span>
    </div>`).join('') || '<div class="ep dim">No episodes yet.</div>';
}
async function open_(name) {
  current = name;
  const e = await j('/api/episode/' + name);
  document.getElementById('detail').innerHTML =
    `<h3>${e.task || e.name}</h3>` + e.timeline.map(t => {
      if (t.t === 'gate') return `<div class="row">GATE ${t.action}
        <span class="dim">${t.approved ? 'allowed' : 'denied'}
        ${t.tainted.length ? ' - untrusted content in scope' : ''}</span></div>`;
      if (t.t === 'exec') return `<div class="row">RUN ${t.action}
        <span class="dim">${t.ok ? 'ok' : 'failed'}</span></div>`;
      if (t.t === 'incident') return `<div class="row">INCIDENT ${t.action}
        <span class="dim">${t.claimed.join(', ')}</span></div>`;
      if (t.t === 'escalation') return `<div class="row">ESCALATED
        <span class="dim">${t.reason}</span></div>`;
      if (t.t === 'end') return `<div class="row">END
        <span class="dim">${t.success ? 'goal reached' : 'goal not reached'}
        - ${t.steps} steps</span></div>`;
      return '';
    }).join('');
}
episodes(); gates();
setInterval(gates, 1000);
setInterval(() => { episodes(); if (current) open_(current); }, 4000);
</script>
"""


def serve(root: Path, approver: DesktopApprover | None = None,
          host: str = "127.0.0.1", port: int = 8129):
    """Start the control surface. Returns (server, approver, url)."""
    approver = approver or DesktopApprover()
    Handler.state = State(Path(root).resolve(), approver)
    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, approver, f"http://{host}:{port}"
