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
import subprocess
import threading
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
                 timeout: int = 20):
        self.name = name
        self.risk = risk if risk in ("read", "write", "exec", "network",
                                     "irreversible") else "network"
        self._timeout = timeout
        self._id = 0
        self._lock = threading.Lock()
        self._proc = subprocess.Popen(
            [str(c) for c in command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1)
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
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _rpc(self, method: str, params: dict) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params})
            timer = threading.Timer(self._timeout, self._proc.kill)
            timer.start()
            try:
                while True:
                    line = self._proc.stdout.readline()
                    if not line:
                        raise RuntimeError(
                            f"mcp server {self.name!r}: no response to "
                            f"{method} (timeout or crash)")
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # tolerate stray output
                    if msg.get("id") != rid:
                        continue  # notification or unrelated
                    if "error" in msg:
                        err = msg["error"]
                        raise RuntimeError(
                            f"mcp server {self.name!r}: {method} -> "
                            f"{err.get('message', err)}")
                    return msg.get("result") or {}
            finally:
                timer.cancel()

    # -- calls ---------------------------------------------------------------
    def call(self, tool: str, arguments: dict) -> str:
        result = self._rpc("tools/call",
                           {"name": tool, "arguments": arguments or {}})
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
                timeout=int(spec.get("timeout", 20)))
        except Exception as exc:
            if on_error:
                on_error(f"mcp server {name!r} failed to start: {exc}")
    return servers
