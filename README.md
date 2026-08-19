# LEDGER

A GATS-planned, audit-first autonomous coding agent. See `DESIGN.md` for the
full design; the framework is GATS (arXiv:2607.08894) — Graph-Augmented Tree
Search with layered world models.

The LLM writes the code. It never decides what happens next, and it is never
believed: deterministic sensors produce the propositions that form state, so a
model claiming "tests pass" while the suite is red becomes a journaled
incident rather than a state transition. Every episode replays bit-exact.

```
pip install -e .[dev]
pytest                      # 170 tests
python -m ledger.cli demo   # paper's 100-task suite: SR / optimality / calls / variance
```

## Getting started

```
ledger doctor                       # check git, claude CLI, repo, config
ledger init                         # write .ledger/config.json
ledger run "fix the failing test"   # plan, author a patch, apply, verify, commit
ledger run "..." --yes              # unattended: pre-approve gated actions
ledger run "..." --json             # CI: one JSON result object + exit code
ledger run "... and push" --push    # adds a network-gated PushBranch action
ledger run "... open a PR" --pr     # adds PushBranch + CreatePR (gh)
ledger run "..." --resume           # re-enter the episode: worktree + warm graph
ledger run "..." --attempts 3 --yes # best-of-N in parallel worktrees
ledger run "..." --deny PushBranch  # per-invocation policy rule
ledger review                       # LLM review of your working-tree diff
ledger status .ledger/episodes/episode/episode.jsonl
```

`run` needs an LLM backend for the patch-authoring skill and the goal
compiler (`--backend claude` uses the local `claude` CLI). The planner itself
makes zero model calls. Patch prompts carry the failing suite output, a
deterministic selection of relevant files (no embeddings — same repo state,
same files), and layered `AGENTS.md` / `LEDGER.md` instructions
(`~/.ledger/AGENTS.md` first, repo root second — the closer file wins).
Journaled command output is secret-redacted (bearer/API tokens, GitHub/AWS
keys, private-key blocks) before it is content-addressed.

## Autonomy profiles (risk gates)

Every action carries a declared risk class; the profile decides which classes
stop for approval at *execution* (search may still plan through them):

| profile | stops on |
|---|---|
| `attended` (default) | write, exec, network, irreversible |
| `supervised` | network, irreversible |
| `autonomous` | irreversible |

`--profile` (or `profile` in `.ledger/config.json`) selects one; `--yes`
pre-approves everything for unattended runs. A denial is not a crash: it is
journaled, observed as a non-effect, and the planner routes around it. If no
route remains, the episode ends with a journaled `ESCALATION` (reason:
`gate_denied`, `budget_exhausted`, `max_steps`, or `no_path`). Patch
authoring is metered per episode (`--max-author-attempts`, default 6) — a
persistently failing model exhausts its budget and escalates instead of
looping.

On top of profiles sit **policy rules** and **hooks** (config.json):

```jsonc
"policy": {"rules": [
  {"action": "PushBranch", "decision": "deny",
   "reason": "publishing is a human's job here"},
  {"action": "RunSuite", "decision": "allow"}
]},
"hooks": [
  {"event": "pre_action", "match": "Commit", "gate": true,
   "command": ["python", "tools/lint_gate.py"]},
  {"event": "post_action", "match": "*",
   "command": ["python", "tools/notify.py"]}
]
```

Rules are first-match with an audit `reason` that lands in the journal's
GATE entry; a deny outranks `--yes`. `--allow`/`--deny` flags prepend
per-invocation rules. A `gate: true` pre-action hook blocks the action on
nonzero exit (fail closed); every hook run is journaled with its output
blob. `ledger doctor` lints the whole config.

## One session for everything

```
ledger chat                                  # THE session: talk AND change code
ledger chat --ask "what does planner.py do?" # one-shot question
ledger chat --journal chat.jsonl             # hash-chained conversation log
```

`ledger chat` is the unified surface. Ask a question and you get an answer;
say what you want changed and LEDGER routes it, confirms in one keystroke,
and runs a verified, journaled episode inline — episode names, resume of
failed attempts, and config are all automatic. `/do <task>` skips the
router. The conversation journal records which turn launched which episode.
Conversation itself never mutates the repo; only the planner does.

On an interactive terminal the session gets the full TUI: a framed input
field with live `/command` autocomplete (Tab completes and cycles), input
history, and a **live working indicator** while the model or an episode
runs — it names the phase it is actually in (`compiling the goal contract`,
`authoring a patch for slot a`, `running the unit suite`, `step 3: Commit`),
counts elapsed time, and Ctrl+C interrupts the work without ending the
session. `ledger run` shows the same indicator (suppressed under `--json`).

**MCP tools.** Configure servers in `.ledger/config.json` and the session
can call their tools mid-conversation — every call is permission-gated
(family `mcp.<server>`, deniable by policy rules) and journaled:

```jsonc
"mcp": {"servers": {
  "docs": {"command": ["python", "tools/docs_server.py"], "risk": "network"}
}}
```

## Talking to it

```
ledger voice                 # the unified session, hands-free
ledger chat --voice          # same thing
ledger voice --setup         # one-time local install instructions
ledger voice --devices       # list microphones
```

Press Enter to talk, Enter again to stop; or just type — voice is additive,
never a trap. Everything runs locally: `ffmpeg` records, `whisper.cpp`
transcribes, `piper` speaks. No audio leaves the machine.

Two deliberate limits. **Confirmations are keyboard-only** — a mis-heard
"yes" must never be able to approve a push or a pull request, so gate
questions hand the keyboard back and `--voice` refuses to combine with
`--dangerously-skip-permissions`. And **code is never read aloud**: the
terminal keeps the full detail while the spoken channel gets the verdict
("episode fix-the-failing-test, goal reached, zero incidents").

Configure in `.ledger/config.json`:

```jsonc
"voice": {"tts": "piper", "device": "Headset Microphone", "speak": true}
```

`ledger doctor` reports recorder, speech-to-text, speech, and microphone.

## Model backends

The claude CLI is the default; any prominent API or local model works:

```
ledger chat --backend anthropic --model claude-sonnet-5     # ANTHROPIC_API_KEY
ledger run "..." --backend openai --model gpt-5.2           # OPENAI_API_KEY
ledger chat --backend ollama --model qwen3-coder            # local, no key
ledger run "..." --backend openai --model kimi-k3 \
  --base-url https://api.kimi.ai/v1                         # any compatible API
```

Or persist it: `"backend": {"kind": "ollama", "model": "qwen3-coder"}` in
config.json. Per-agent `"backend"`/`"model"`/`"base_url"` in session specs
bind cheap models to fan-out work. `--dangerously-skip-permissions` exists
for sandboxes where a wrong action is cheap — it bypasses every gate,
prompt, and policy rule, and says so in red.

## Parallel agents in one session

```
ledger session agents.json --workers 4
```

```json
[
  {"name": "fix-auth",  "task": "fix the 401 on expired tokens",
   "repo": ".", "test": ["pytest", "-q", "tests/auth"]},
  {"name": "fix-cache", "task": "cache invalidation drops stale keys",
   "repo": ".", "test": ["pytest", "-q", "tests/cache"]}
]
```

Each agent gets its own git worktree, branch, episode directory and journal —
nothing one agent does can corrupt another. What they share is the transition
graph: verified edges and UCB statistics are pooled after every agent
finishes. Agents complete in arbitrary order, but the merge runs in sorted
agent-name order, so the shared graph is a pure function of the results, and
the merge order is journaled.

Not yet shared: L2 statistics are seeded read-only and not merged back
(per-state counts across repos are not safely additive until the state
abstraction of `DESIGN.md` §12.8 is fixed). Agents share structure, not yet
learned dynamics.

## Audit

```
ledger status <journal>       # goal, plan, incidents, divergences, chain health
ledger why <journal> <step>   # the UCB table that actually produced the choice
ledger vis <journal>          # one beautiful self-contained HTML report
ledger incidents <journal>    # claims the sensors refused to confirm
ledger replay <journal>       # bit-exact planner replay
ledger verify-chain <journal> # tamper-evidence
ledger stats <graph.sqlite>   # nodes, edges, dead ends, layer breakdown
```

Terminal output is styled on interactive terminals (verdict headlines,
colored plan chains, aligned decision tables, escalation-aware `next:`
hints) and degrades to exactly the old plain text when piped — CI logs,
`--json`, and scripts are unaffected. Honors `NO_COLOR` / `LEDGER_COLOR`.
`ledger vis` renders any episode journal into a single dark-themed HTML
file — timeline of every PLAN/GATE/HOOK/EXEC/OBSERVE entry, expandable
decision tables, state diffs, incident and escalation banners — with no
external assets, so it works from disk and in CI artifacts forever.

`why` reports the decision inputs — Q, N, UCB, world-model layer and
confidence per arm — not a model's after-the-fact explanation of itself.

## Status

**M4.** Core port (M1), real-repo execution layer (M2: worktree sandbox,
skills, fingerprint-memoized sensors, GATE/EXEC journaling, lie-detection
INCIDENTs), learned/generative layers (M3: journal-mined L2, pluggable L3
with an Eq. 8 cache that drives planning calls to zero on repeat episodes,
goal compiler), the LLM patch-authoring skill, the multi-agent session
layer, and M4: autonomy profiles with risk gates, survivable denials,
journaled escalation, and per-episode authoring budgets.

Authored diffs are validated with `git apply --check` before they are allowed
into the queue, so a malformed or hallucinated patch produces a plain
non-effect the planner re-plans around rather than a corrupted worktree.

`CODEX_PARITY.md` maps LEDGER against the OpenAI Codex capability set —
what's matched, what's next (PR flow, AGENTS.md hierarchy, `--json` output,
OS-level sandboxing), and what LEDGER deliberately does differently.

Milestones (DESIGN.md §13/§14.7): M1 core → M2 executor/sandbox/divergence →
M3 L2/L3/goal compiler → M4 risk gates → **M5 eval harness** (next) → M6–M9
RL roadmap.
