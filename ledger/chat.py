"""Conversational layer (design SS7.1 adjacent).

Chat is deliberately *outside* the planner: talking to the model never
mutates repo state, never enters propositions, and never consumes planning
budget. What it does share with the agent is the audit surface -- every turn
is appended to a hash-chained journal, so "what did I ask it, what did it
say, and which of those turns led to an episode" is answerable after the
fact. That is the property no transcript-based assistant provides.

Backends are single-shot (`complete(prompt) -> str`), so history is rendered
into the prompt deterministically rather than held server-side.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .journal import Journal
from .skills_llm import project_instructions, read_files, repo_tree

SYSTEM = """You are LEDGER, an audit-first coding assistant.

You are talking to an engineer about a specific repository. Answer concretely
and briefly. When you are uncertain about the contents of a file you have not
been shown, say so rather than guessing -- claims you cannot support are the
one thing this system exists to catch.

Change requests are executed right here in this session: the deterministic
planner runs a verified, journaled episode and reports back. You never apply
edits yourself in conversation -- the planner owns edits so they stay
verified and replayable."""

CLASSIFY_PROMPT = """You route messages inside a coding agent's session.
Decide whether the user's message asks for the repository to be CHANGED
(fix, add, implement, refactor, update, remove code; make tests pass) or is
conversation ABOUT it (questions, explanations, opinions, greetings).

User message:
{text}

Respond with ONLY this JSON:
{{"intent": "change" or "chat",
 "task": "<one-line imperative restatement if change, else empty>"}}"""

MAX_TURNS_IN_PROMPT = 12


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str


TOOLS_PROTOCOL = """AVAILABLE TOOLS
{tools}

To use a tool, reply with ONLY one JSON object (no prose around it):
{{"tool": "<server:name>", "arguments": {{...}}}}
You will be shown the result and can then call another tool or answer.
Use tools only when they genuinely help answer the user."""

MAX_TOOL_CALLS_PER_TURN = 5


@dataclass
class Conversation:
    """Rendered-transcript conversation over a single-shot backend."""

    backend: object
    system: str = SYSTEM
    turns: list[Turn] = field(default_factory=list)
    context: str = ""
    journal: Journal | None = None
    # MCP surface: qualified "server:tool" -> description, and the gated
    # executor closure (qualified, arguments) -> result text.
    tools: dict = field(default_factory=dict)
    tool_call: object = None

    def add_repo_context(self, root: Path, files: list[str] | None = None) -> None:
        parts = []
        instructions = project_instructions(Path(root))
        if instructions:
            parts.append("PROJECT INSTRUCTIONS (from the repository)\n"
                         + instructions)
        parts.append(f"REPOSITORY FILES\n{repo_tree(Path(root))}")
        if files:
            parts.append("FILE CONTENTS\n" + read_files(Path(root), files))
        self.context = "\n\n".join(parts)

    def render(self, user_text: str) -> str:
        blocks = [self.system]
        if self.context:
            blocks.append(self.context)
        if self.tools:
            listing = "\n".join(f"- {name}: {desc}"
                                for name, desc in sorted(self.tools.items()))
            blocks.append(TOOLS_PROTOCOL.format(tools=listing))
        recent = self.turns[-MAX_TURNS_IN_PROMPT:]
        if recent:
            transcript = "\n\n".join(
                f"{'User' if t.role == 'user' else 'LEDGER'}: {t.text}"
                for t in recent
            )
            blocks.append("CONVERSATION SO FAR\n" + transcript)
        blocks.append(f"User: {user_text}\nLEDGER:")
        return "\n\n".join(blocks)

    @staticmethod
    def _tool_request(reply: str) -> tuple[str, dict] | None:
        """A tool call is a reply that IS one JSON object with a 'tool'
        key -- anything else is prose for the user."""
        stripped = reply.strip()
        if not stripped.startswith("{"):
            return None
        from .llm import extract_json
        obj = extract_json(stripped)
        if not obj or "tool" not in obj:
            return None
        args = obj.get("arguments")
        return str(obj["tool"]), (args if isinstance(args, dict) else {})

    def ask(self, user_text: str, progress=None) -> str:
        def say(text):
            if progress is not None:
                try:
                    progress(text)
                except Exception:
                    pass

        if self.journal is not None:
            self.journal.append("CHAT_USER", text=user_text)
        pending = user_text
        reply = ""
        for _ in range(MAX_TOOL_CALLS_PER_TURN + 1):
            reply = self.backend.complete(self.render(pending)).strip()
            self.turns.append(Turn("user", pending))
            call = self._tool_request(reply) if self.tool_call else None
            if call is None:
                break
            name, args = call
            say(f"calling tool {name}")
            if self.journal is not None:
                self.journal.append("MCP_CALL", tool=name, arguments=args)
            result = self.tool_call(name, args)
            say("thinking about the tool result")
            if self.journal is not None:
                self.journal.append(
                    "MCP_RESULT", tool=name, chars=len(result),
                    denied=result.startswith("DENIED"))
            self.turns.append(Turn("assistant",
                                   f"[tool] {name} {json.dumps(args)}"))
            pending = (f"TOOL RESULT {name}:\n{result[:4000]}\n\n"
                       "Continue: answer the user, or call another tool.")
        self.turns.append(Turn("assistant", reply))
        if self.journal is not None:
            self.journal.append("CHAT_ASSISTANT", text=reply,
                                backend=getattr(self.backend, "id", "?"))
        return reply

    def classify(self, user_text: str) -> tuple[str, str]:
        """Route a message: ("change", task) or ("chat", ""). Fails safe --
        anything unparseable is conversation, never an accidental episode."""
        from .llm import extract_json
        try:
            obj = extract_json(self.backend.complete(
                CLASSIFY_PROMPT.format(text=user_text)))
        except Exception:
            return "chat", ""
        if not obj or obj.get("intent") != "change":
            return "chat", ""
        task = str(obj.get("task") or "").strip() or user_text.strip()
        return "change", task

    def record_episode(self, user_text: str, task: str, outcome: dict,
                       summary: str) -> None:
        """Link conversation and episode in both directions: journal entries
        answer 'which turn launched which episode', and the transcript keeps
        the result so follow-up questions have it as context."""
        if self.journal is not None:
            self.journal.append("EPISODE_LAUNCHED", text=user_text, task=task,
                                journal=str(outcome.get("journal", "")))
            self.journal.append("EPISODE_RESULT",
                                success=bool(outcome.get("success")),
                                plan=list(outcome.get("plan", [])),
                                escalation=outcome.get("escalation"))
        self.turns.append(Turn("user", user_text))
        self.turns.append(Turn("assistant", summary))

    def transcript(self) -> str:
        return "\n\n".join(
            f"{'You' if t.role == 'user' else 'LEDGER'}: {t.text}"
            for t in self.turns
        )


HELP = """Commands:
  /help              show this
  /do <task>         run a change episode without the intent check
  /tools             list connected MCP tools
  /context <files>   inline file contents into the conversation context
  /tree              re-read the repository file list
  /clear             forget the conversation (keeps repo context)
  /save <path>       write the transcript to a file
  /exit              leave chat
Plain messages are routed automatically: questions get answers, change
requests run as verified, journaled episodes -- right here."""


def _run_change(conv: Conversation, runner, user_text: str, task: str,
                input_fn, output_fn, confirm: bool, busy) -> None:
    if confirm:
        try:
            resp = input_fn(f"run episode for: {task!r}? [Y/n] ")
        except (EOFError, KeyboardInterrupt):
            output_fn("cancelled")
            return
        if resp.strip().lower() in ("n", "no"):
            output_fn("not running it -- rephrase or use /do <task>")
            return
    try:
        with busy("starting the episode") as progress:
            outcome = runner(task, progress=progress)
    except KeyboardInterrupt:
        # Ctrl+C stops the work, not the session -- and the journal written
        # so far still verifies and replays.
        output_fn("interrupted — the episode stopped; its journal so far "
                  "is still valid")
        return
    except Exception as exc:
        output_fn(f"episode failed to start: {exc}")
        return
    summary = outcome.get("summary_plain", "")
    output_fn(outcome.get("summary_styled", summary))
    conv.record_episode(user_text, task, outcome, summary)


def repl(conv: Conversation, root: Path, *, input_fn=input,
         output_fn=print, runner=None, busy=None, greet=True) -> int:
    """Interactive loop -- the one session for everything. `input_fn` /
    `output_fn` are injected so the loop is testable without a terminal;
    `runner` (task_text -> outcome dict) executes change episodes inline;
    `busy(label)` is an optional progress context manager (the spinner).
    """
    # One implementation everywhere: the spinner yields a live Progress
    # handle and simply renders nothing when the output is not a terminal,
    # so callers always get a working handle.
    if busy is None:
        from .tui import spinner as busy
    if greet:  # the TUI banner already says this
        if runner is not None:
            output_fn("LEDGER. Ask about the repo, or just say what to "
                      "change -- changes run as verified, journaled "
                      "episodes right here. /help for commands.")
        else:
            output_fn("LEDGER chat. /help for commands, /exit to leave.")
    while True:
        try:
            line = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("")
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/help":
            output_fn(HELP)
            continue
        if line == "/clear":
            conv.turns.clear()
            output_fn("conversation cleared")
            continue
        if line == "/tree":
            conv.add_repo_context(root)
            output_fn("repository context refreshed")
            continue
        if line == "/tools":
            if not conv.tools:
                output_fn('no MCP tools connected -- configure '
                          '"mcp": {"servers": {...}} in .ledger/config.json')
            else:
                for name, desc in sorted(conv.tools.items()):
                    output_fn(f"  {name}  {desc}")
            continue
        if line.startswith("/context"):
            files = line.split()[1:]
            if not files:
                output_fn("usage: /context <path> [path ...]")
                continue
            conv.add_repo_context(root, files)
            output_fn(f"context now includes {len(files)} file(s)")
            continue
        if line.startswith("/save"):
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                output_fn("usage: /save <path>")
                continue
            Path(parts[1]).write_text(conv.transcript(), encoding="utf-8")
            output_fn(f"transcript written to {parts[1]}")
            continue
        if line.startswith("/do"):
            task = line[3:].strip()
            if not task:
                output_fn("usage: /do <what you want changed>")
                continue
            if runner is None:
                output_fn("change episodes are unavailable here "
                          "(not a git repository)")
                continue
            _run_change(conv, runner, line, task, input_fn, output_fn,
                        confirm=False, busy=busy)
            continue
        if line.startswith("/"):
            output_fn(f"unknown command {line.split()[0]!r} -- /help for the list")
            continue
        if runner is not None:
            with busy("reading your request"):
                intent, task = conv.classify(line)
            if intent == "change":
                _run_change(conv, runner, line, task, input_fn, output_fn,
                            confirm=True, busy=busy)
                continue
        try:
            with busy("thinking") as progress:
                reply = conv.ask(line, progress=progress)
            output_fn(reply)
        except KeyboardInterrupt:
            output_fn("interrupted")
        except Exception as exc:  # backend failures must not kill the session
            output_fn(f"backend error: {exc}")
    return 0
