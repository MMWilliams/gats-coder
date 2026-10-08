"""MCP client (stdio transport): newline-delimited JSON-RPC 2.0.

Servers come from `.ledger/config.json`:

    "mcp": {"servers": {
        "docs": {"command": ["python", "tools/docs_server.py"],
                 "risk": "network"}}}

Tools surface in the unified session as `server:tool`. Every call is gated
like any other risky action (family `mcp.<server>`, deniable by policy
rules) and journaled -- an MCP tool is a capability, never a trust grant.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading

from . import sandbox
from dataclasses import dataclass, field


@dataclass
class MCPTool:
    name: str
    description: str = ""
    schema: dict = field(default_factory=dict)


class MCPServer:
    """One stdio MCP server: spawn, handshake, list tools, call tools."""

    PROTOCOL = "2025-06-18"

    def __init__(self, name: str, command: list[str], risk: str = "network",
                 timeout: int = 20, call_timeout: int = 300):
        self.name = name
        self.risk = risk if risk in ("read", "write", "exec", "network",
                                     "irreversible") else "network"
        # Starting a server and running a tool are different waits (codex:
        # 30s / 300s). A slow tool is cancelled; only a dead server is fatal.
        self._timeout = timeout
        self._call_timeout = call_timeout
        self._id = 0
        self._lock = threading.Lock()
        self._replies: dict = {}
        self._arrived = threading.Condition()
        self._eof = False
        self._wire = threading.Lock()
        # In its own job: closing the server kills whatever it spawned too.
        self._job = sandbox.popen(
            [str(c) for c in command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1)
        self._proc = self._job.proc
        threading.Thread(target=self._read, daemon=True).start()
        self._rpc("initialize", {
            "protocolVersion": self.PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "ledger", "version": "0"}})
        self._notify("notifications/initialized", {})
        listed = self._rpc("tools/list", {})
        self.tools = [MCPTool(t.get("name", "?"), t.get("description", ""),
                              t.get("inputSchema", {}))
                      for t in listed.get("tools", [])]

    # -- wire ----------------------------------------------------------------
    def _send(self, msg: dict) -> None:
        with self._wire:
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _read(self) -> None:
        """Route every response to its id; stray output is tolerated."""
        for line in self._proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and "id" in msg:
                with self._arrived:
                    self._replies[msg["id"]] = msg
                    self._arrived.notify_all()
        with self._arrived:
            self._eof = True
            self._arrived.notify_all()

    def _rpc(self, method: str, params: dict, timeout: float | None = None) -> dict:
        timeout = self._timeout if timeout is None else timeout
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params})
        with self._arrived:
            got = self._arrived.wait_for(
                lambda: rid in self._replies or self._eof, timeout)
            msg = self._replies.pop(rid, None)
        if msg is None:
            if not got:
                # Cancel and carry on (goose); the server stays usable.
                try:
                    self._notify("notifications/cancelled",
                                 {"requestId": rid, "reason": "timeout"})
                except OSError:
                    pass
                raise RuntimeError(f"mcp server {self.name!r}: {method} timed "
                                   f"out after {timeout}s (cancelled)")
            raise RuntimeError(f"mcp server {self.name!r}: no response to "
                               f"{method} (server exited)")
        if "error" in msg:
            err = msg["error"]
            raise RuntimeError(f"mcp server {self.name!r}: {method} -> "
                               f"{err.get('message', err) if isinstance(err, dict) else err}")
        return msg.get("result") or {}

    # -- calls ---------------------------------------------------------------
    def call(self, tool: str, arguments: dict) -> str:
        result = self._rpc("tools/call",
                           {"name": tool, "arguments": arguments or {}},
                           timeout=self._call_timeout)
        parts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        text = "\n".join(p for p in parts if p) or json.dumps(result)
        if result.get("isError"):
            return f"TOOL ERROR: {text}"
        return text

    def close(self) -> None:
        try:
            self._proc.terminate()
        except OSError:
            pass
        self._job.close()


def load_servers(cfg: dict, on_error=None) -> dict[str, MCPServer]:
    """Start every configured server; a server that fails to start is
    reported and skipped, never fatal."""
    block = cfg.get("mcp")
    block = block.get("servers", {}) if isinstance(block, dict) else {}
    servers: dict[str, MCPServer] = {}
    for name, spec in block.items():
        if not isinstance(spec, dict) or not spec.get("command"):
            continue
        try:
            servers[name] = MCPServer(
                str(name), list(spec["command"]),
                risk=str(spec.get("risk", "network")),
                timeout=int(spec.get("timeout", 20)),
                call_timeout=int(spec.get("call_timeout", 300)))
        except Exception as exc:
            if on_error:
                on_error(f"mcp server {name!r} failed to start: {exc}")
    return servers


# ---------------------------------------------------------------------------
# MCP tools as contracted planner actions
# ---------------------------------------------------------------------------
#
# Surfacing a tool to `chat` is easy and nearly worthless for audit: the model
# decides what to call and the only record is that it happened. For a tool call
# to be part of a VERIFIED episode it has to be an action like any other -- a
# declared risk the gate can stop on, preconditions the planner can reason
# about, and effects an instrument can check afterwards.
#
# Three rules make that work, and each exists because the alternative
# reintroduces the free tool loop this design refuses:
#
# 1. **Only tools the operator contracted become actions.** An MCP server can
#    advertise anything; the planner reaches only what `config.json` names.
# 2. **The planner chooses actions, not arguments.** Arguments are fixed in
#    config. A model choosing arguments is a model deciding what happens, and
#    then nothing downstream is verifiable.
# 3. **Effects are claims, not facts.** The skill reports the contracted
#    propositions and the sensors decide. A tool that reports success without
#    moving the world produces an INCIDENT, exactly like a model claiming
#    tests pass over a red suite.

from .actions import RISK_LEVELS, Action


class MCPToolSkill:
    """One contracted MCP tool, run as a plan step.

    Claims the contracted effects on success and nothing on failure, which is
    the same bargain every other skill makes. The tool's own text is recorded
    as a freeform claim and never believed.
    """

    def __init__(self, server, tool: str, arguments: dict,
                 claims: frozenset, provenance=None, variables=None):
        self.server = server
        self.tool = tool
        self.arguments = expand(dict(arguments or {}), variables or {})
        self.claimed = claims
        self.provenance = provenance

    def run(self, ctx):
        from .executor import SkillResult, redact_secrets

        label = f"mcp.{self.server.name}:{self.tool}"
        ctx.say(f"calling {label}")
        try:
            text = self.server.call(self.tool, self.arguments)
        except Exception as exc:                        # noqa: BLE001
            return SkillResult(ok=False, claimed_props=frozenset(),
                               claims=[f"{label} could not run: {exc}"])

        # The result is words from outside the trust boundary. Recording it
        # BEFORE returning means the very next gate already knows, rather than
        # one action later.
        if self.provenance is not None:
            self.provenance.note(f"mcp:{label}")

        failed = text.startswith("TOOL ERROR")
        blob = ctx.blobs.put(redact_secrets(text))
        return SkillResult(
            ok=not failed,
            claimed_props=frozenset() if failed else self.claimed,
            claims=[f"{label} " + ("failed" if failed else "reported success")],
            commands=[{"cmd": label, "exit": 1 if failed else 0, "out": blob}],
        )


def expand(value, variables: dict):
    """`${name}` -> variables[name] in every string of a config value.

    Only episode facts the runner fixed before planning (branch, episode,
    worktree), never state or model output: the planner still chooses
    actions, not arguments. An unknown name is left as written.
    """
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}",
                      lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: expand(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, variables) for v in value]
    return value


def _worst(a: str, b: str) -> str:
    """The more dangerous of two risk classes."""
    order = list(RISK_LEVELS)
    return a if order.index(a) >= order.index(b) else b


def contracted_actions(servers: dict, cfg: dict, vocabulary,
                       provenance=None, on_error=None, variables=None):
    """(actions, skills) for every tool the config contracts.

    Rejects rather than degrades. A tool whose effects are not in the sensor
    vocabulary cannot be verified, and an unverifiable action in an audited
    episode is worse than a missing one, so it is reported and skipped.
    """
    block = cfg.get("mcp")
    block = block.get("servers", {}) if isinstance(block, dict) else {}
    known = set(vocabulary)
    actions, skills = [], {}

    for server_name, spec in block.items():
        server = servers.get(server_name)
        if server is None or not isinstance(spec, dict):
            continue
        offered = {t.name for t in server.tools}
        server_risk = str(spec.get("risk", "network"))
        contracts = spec.get("tools")
        if not isinstance(contracts, dict):
            continue

        for tool_name, contract in contracts.items():
            label = f"mcp.{server_name}:{tool_name}"
            if not isinstance(contract, dict):
                continue
            if tool_name not in offered:
                if on_error:
                    on_error(f"{label}: the server does not offer that tool")
                continue

            effects = frozenset(str(e) for e in contract.get("effects") or ())
            if not effects:
                # With no effects the planner cannot model it, useful_actions
                # prunes it as a predicted no-op, and it would never be chosen.
                # Saying so beats shipping an action that silently never runs.
                if on_error:
                    on_error(f"{label}: no contracted effects, so the planner "
                             "cannot reach it. It stays available in chat.")
                continue

            unknown = sorted(effects - known)
            if unknown:
                if on_error:
                    on_error(f"{label}: effects {unknown} are not in the "
                             "sensor vocabulary, so nothing could verify "
                             "them. Skipped.")
                continue

            prec = frozenset(str(p) for p in contract.get("prec") or ())
            stray = sorted(prec - known)
            if stray:
                if on_error:
                    on_error(f"{label}: preconditions {stray} are not sensor "
                             "propositions. Skipped.")
                continue

            # A tool may declare itself MORE dangerous than its server, never
            # less: the server's risk is the operator's floor for everything
            # it exposes.
            risk = _worst(server_risk, str(contract.get("risk", server_risk)))

            actions.append(Action(
                name=label, prec=prec, add=effects, risk=risk,
                # A remote call over a pipe to somebody else's process is not
                # deterministic, so L1's contract is the optimistic outcome and
                # L2 learns what actually happens per state.
                deterministic=False,
            ))
            skills[label] = MCPToolSkill(
                server, tool_name, contract.get("arguments") or {},
                effects, provenance=provenance, variables=variables)

    return actions, skills
