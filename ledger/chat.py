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

from dataclasses import dataclass, field
from pathlib import Path

from .journal import Journal
from .skills_llm import read_files, repo_tree

SYSTEM = """You are LEDGER, an audit-first coding assistant.

You are talking to an engineer about a specific repository. Answer concretely
and briefly. When you are uncertain about the contents of a file you have not
been shown, say so rather than guessing -- claims you cannot support are the
one thing this system exists to catch.

When the user asks you to actually change the code, tell them to run
`ledger run` with a task description; you are the conversational surface, and
the planner owns edits so they stay verified and replayable."""

MAX_TURNS_IN_PROMPT = 12


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str


@dataclass
class Conversation:
    """Rendered-transcript conversation over a single-shot backend."""

    backend: object
    system: str = SYSTEM
    turns: list[Turn] = field(default_factory=list)
    context: str = ""
    journal: Journal | None = None

    def add_repo_context(self, root: Path, files: list[str] | None = None) -> None:
        parts = [f"REPOSITORY FILES\n{repo_tree(Path(root))}"]
        if files:
            parts.append("FILE CONTENTS\n" + read_files(Path(root), files))
        self.context = "\n\n".join(parts)

    def render(self, user_text: str) -> str:
        blocks = [self.system]
        if self.context:
            blocks.append(self.context)
        recent = self.turns[-MAX_TURNS_IN_PROMPT:]
        if recent:
            transcript = "\n\n".join(
                f"{'User' if t.role == 'user' else 'LEDGER'}: {t.text}"
                for t in recent
            )
            blocks.append("CONVERSATION SO FAR\n" + transcript)
        blocks.append(f"User: {user_text}\nLEDGER:")
        return "\n\n".join(blocks)

    def ask(self, user_text: str) -> str:
        prompt = self.render(user_text)
        if self.journal is not None:
            self.journal.append("CHAT_USER", text=user_text,
                                prompt_chars=len(prompt))
        reply = self.backend.complete(prompt).strip()
        self.turns.append(Turn("user", user_text))
        self.turns.append(Turn("assistant", reply))
        if self.journal is not None:
            self.journal.append("CHAT_ASSISTANT", text=reply,
                                backend=getattr(self.backend, "id", "?"))
        return reply

    def transcript(self) -> str:
        return "\n\n".join(
            f"{'You' if t.role == 'user' else 'LEDGER'}: {t.text}"
            for t in self.turns
        )


HELP = """Commands:
  /help              show this
  /context <files>   inline file contents into the conversation context
  /tree              re-read the repository file list
  /clear             forget the conversation (keeps repo context)
  /save <path>       write the transcript to a file
  /exit              leave chat
Anything else is sent to the model."""


def repl(conv: Conversation, root: Path, *, input_fn=input,
         output_fn=print) -> int:
    """Interactive loop. `input_fn`/`output_fn` are injected so the loop is
    testable without a terminal."""
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
        if line.startswith("/"):
            output_fn(f"unknown command {line.split()[0]!r} -- /help for the list")
            continue
        try:
            output_fn(conv.ask(line))
        except Exception as exc:  # backend failures must not kill the session
            output_fn(f"backend error: {exc}")
    return 0
