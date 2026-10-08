"""What is in scope that nobody vouched for.

Prompt injection is not a model defect you can prompt your way out of; it
has the shape of SQL injection -- untrusted data reaching a layer that
decides to act. What fixed SQL injection was not better sanitising, it was
keeping data structurally separate from code. The analogue here is narrow
and mechanical: once content from outside the episode's trust boundary has
been read, the autonomy profile stops applying and every action that is not
a read goes back in front of a human.

That is deliberately blunt. A web page LEDGER fetched to learn an API is
indistinguishable, at the gate, from a web page that asks it to push a
branch, because the only honest thing the gate knows is that neither one
was written by the principal.

Two independent paths record the same fact. `Provenance` is live and drives
the gate. `external_sources` re-derives it from the journal afterwards, so a
receipt can state what was in scope at each step -- and so that the two
disagreeing is itself a finding rather than a silent gap.
"""

from __future__ import annotations

# Under taint every non-read action is escalated, which makes `autonomous`
# behave like `attended` until the episode ends. Risk classes, not action
# names, so a new action inherits the rule by declaring its risk.
TAINT_RISKS = frozenset({"write", "exec", "network", "irreversible"})


def external_sources(entry: dict) -> list[str]:
    """The untrusted content a journal entry brought into the episode.

    Only ingress counts. A network action that *sends* (PushBranch,
    CreatePR) is dangerous for other reasons and is gated on its risk; it
    does not put words in the model's context, so it does not taint.
    """
    kind = entry.get("t")
    if kind == "HEADER":
        # Taint already in scope when the episode began -- a chat session
        # that read an MCP tool result and then asked for a change hands it
        # down. Recorded at the head of the chain so the episode's own
        # receipt can explain where its escalations came from, instead of
        # pointing at a conversation the reader does not have.
        return [str(s) for s in (entry.get("inherited_taint") or [])]
    if kind == "RESEARCH":
        return [str(s) for s in (entry.get("sources") or [])]
    if kind == "MCP_RESULT" and not entry.get("denied"):
        return [f"mcp:{entry.get('tool')}"]
    if kind == "EXEC" and str(entry.get("action", "")).startswith("mcp."):
        # A contracted MCP tool run as a plan step. It both sends and reads,
        # and it is the reading that matters: the reply is words from
        # somebody else's process, so everything after it is escalated.
        return [f"mcp:{entry.get('action')}"]
    return []


class Provenance:
    """Untrusted sources read so far, in order, deduplicated.

    Callable so it can be handed straight to `PolicyGate(taint_probe=...)`.
    """

    def __init__(self, sources=()):
        self._sources: list[str] = []
        self.note(*sources)

    def note(self, *sources: str) -> None:
        for source in sources:
            text = str(source)
            if text and text not in self._sources:
                self._sources.append(text)

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    @property
    def tainted(self) -> bool:
        return bool(self._sources)

    def __call__(self) -> list[str]:
        return list(self._sources)
