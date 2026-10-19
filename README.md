# LEDGER

A GATS-planned, audit-first autonomous coding agent. See `DESIGN.md` for the
full design; the framework is GATS (arXiv:2607.08894) — Graph-Augmented Tree
Search with layered world models.

The LLM writes the code. It never decides what happens next, and it is never
believed: deterministic sensors produce the propositions that form state, so a
model claiming "tests pass" while the suite is red becomes a journaled
incident rather than a state transition. Every episode replays bit-exact.

## Install

```
curl -fsSL https://kyberos.ai/ledger/install.sh | sh        # macOS, Linux
irm https://kyberos.ai/ledger/install.ps1 | iex             # Windows PowerShell
```

One line, no admin rights. The installer checks the wheel's SHA-256 against
`latest.json` and refuses a mismatch, installs into its own environment
(`~/.ledger/venv`), and puts `ledger` on your PATH. Run it again to upgrade.

## Launch

```
cd your-project
ledger                                   # the session, like `claude` or `codex`
ledger "fix the failing login test"      # start it with a first message
ledger -c                                # continue this project's last session
ledger --resume                          # pick an earlier session from a list
ledger -r 2                              # ...or name it: number, id, or short tag
ledger -p "what does src/app.py do?"     # one answer, no session (scripts, CI)
ledger --dangerously-skip-permissions    # no gates at all (sandboxes only)
```

The first launch walks through choosing a model (Claude CLI, an API key, or a
local model through Ollama). Sessions are saved per project under
`~/.ledger/sessions/` (owner-only, outside the repo), so nothing you discuss
lands in a commit. Inside a session: `/status` shows the model, permissions
and where keys live; `/sessions` lists earlier ones; `/help` the rest.

## Your API keys

A key you give LEDGER stays on your machine and goes to exactly one place:
its provider.

- **Sealed at rest** by the OS: Windows DPAPI (encrypted to your account; a
  copied file is useless elsewhere), the macOS Keychain, or the Linux Secret
  Service. Only where none exists does it fall back to an owner-only file,
  and setup says so. Older plaintext stores are sealed on first read.
- **Sent only to the provider's own API host, over HTTPS** (Anthropic to
  api.anthropic.com, Groq to api.groq.com, ...). A base URL that comes from a
  repository's `.ledger/config.json` never receives a key, so cloning a
  hostile repo and running `ledger` cannot redirect it. Your own gateway?
  Bind it yourself: `ledger setup --key openai --host gateway.example.com`.
- **Never inherited** by anything LEDGER runs for a repository: test suites,
  hooks, MCP servers and launched apps get an environment with every provider
  key removed.
- **Never written or forwarded**: every key LEDGER loads is scrubbed by exact
  value from journals, blobs, receipts, session transcripts, error messages
  and the prompts it sends to a model, whatever the key looks like.
- **Never on the command line**: keys are typed at a hidden prompt
  (`ledger setup --key <provider>`), never accepted as an argument, and shown
  masked (`ledger setup --list-keys`). Remove one with `--forget <provider>`.

## From source

```
pip install -e .[dev]
pytest
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
Journals are secret-redacted on write (every loaded key by exact value, plus
bearer/API tokens, GitHub/AWS keys, private-key blocks); see "Your API keys".

## Autonomy profiles (risk gates)

Every action carries a declared risk class; the profile decides which classes
stop for approval at *execution* (search may still plan through them):

| profile | stops on |
|---|---|
| `attended` (default) | write, exec, network, irreversible |
| `supervised` | network, irreversible |
| `autonomous` | irreversible |

Once the session has read something from outside its trust boundary (a
page the strategist fetched, an MCP tool result), the profile stops
applying and every non-read action is escalated for the rest of the
session, including inside episodes the conversation launches later. The
approval memo is keyed by that state, so a `yes` given before the fetch is
not inherited after it. An explicit `allow` policy rule still wins: that is
the operator deciding in advance, which is the one thing a fetched page
cannot do.

`--profile` (or `profile` in `.ledger/config.json`) selects one; `--yes`
pre-approves everything for unattended runs. A denial is not a crash: it is
journaled, the action is out of the running for the rest of the session,
and the planner routes around it. Nothing is learned from a refusal, so a
"no" does not survive into the persistent graph or the repo's world model,
and resuming with an approving gate picks the work back up. If no route
remains, the episode ends with a journaled `ESCALATION` (reason:
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

Press Enter to talk, Enter again to stop; or just type, voice is additive
and never a trap. Everything runs locally: `ffmpeg` records, `whisper.cpp`
transcribes, and speech comes from **voiceforge** if its daemon is running,
otherwise `piper`, otherwise the OS voice. No audio leaves the machine.

voiceforge is preferred because it is the one engine whose voice identity is
ours outright, and because it watermarks its output by default. It is reached
over loopback rather than imported, so it keeps its own environment and LEDGER
keeps having no dependencies:

```
conda run -n voiceforge python -m runtime.speak_server
```

A daemon that is not running costs you piper, never silence.

Two deliberate limits. **Confirmations are keyboard-only** — a mis-heard
"yes" must never be able to approve a push or a pull request, so gate
questions hand the keyboard back and `--voice` refuses to combine with
`--dangerously-skip-permissions`. And **code is never read aloud**: the
terminal keeps the full detail while the spoken channel gets the verdict
("episode fix-the-failing-test, goal reached, zero incidents").

Configure in `.ledger/config.json`:

```jsonc
"voice": {"tts": "auto", "device": "Headset Microphone", "speak": true}
// tts: auto | voiceforge | piper | sapi | off
```

`ledger doctor` reports recorder, speech-to-text, speech, and microphone.

## The desktop app

```
ledger app                       # control surface + a browser window
ledger app --no-open             # just the surface (the shell opens its own)
ledger run "..." --app-url http://127.0.0.1:8129
```

`ledger app` serves a local control surface on loopback: the episode list, a
run's timeline drawn from its journal, the signed receipt on demand, and the
gate approvals. It is `http.server` and nothing else, so it ships inside the
same wheel with no new dependencies. The desktop shell in `desktop/` is a
Tauri window pointed at it, which starts the surface as a child process and
stops it on close.

**It closes a real hole rather than being a nicer CLI.** The terminal approver
denies when there is no terminal, on the correct reasoning that without one
there is nobody to ask. The consequence is that an episode launched from
anywhere except a shell has every gated action refused and escalates
`gate_denied`. With a window open, the question goes there instead:

```
ledger app                                        # in one window
ledger run "fix the failing test" --app-url ...   # in another
```

The same attended episode that fails with two denials and no terminal
succeeds with the full plan when a window is open. `LEDGER_APP_URL` sets the
address once. Every failure path still denies -- no app, a window closed
mid-question, a question the server has forgotten -- because none of those is
a human saying yes. `--yes` continues to win over the app, since it is an
explicit decision to pre-approve.

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
ledger receipt <episode>      # signed, portable account of what it was allowed to do
ledger verify-receipt <file>  # check one, with or without the journal
ledger key                    # the public key to publish so --key-id can pin you
ledger stats <graph.sqlite>   # nodes, edges, dead ends, layer breakdown
```

The journal is the whole truth and it never leaves the machine that made
it, which is no use to somebody asking what your agent did on their behalf.
`ledger receipt` extracts an episode's claims (policy in force, every
gated decision, every action, every false claim the sensors caught, and
what was in scope that nobody vouched for), signs them Ed25519 (stdlib, no
dependencies) and binds them to each segment's head hash. It is small
enough to email. `ledger verify-receipt` alone answers *the holder of this
key stands behind these claims*; with `--journal` it re-derives the whole
body from the chain and compares, which answers *these claims are that
episode*. `--key-id` pins the issuer, so a valid signature by a stranger
still fails.

The unit is the **episode**, not the file. Resume writes `episode.jsonl`,
`episode.r2.jsonl`, …, and a re-run appends another `HEADER` to the file it
is already in; a receipt covers every run of every segment and reports each
attempt separately, because one that attested only the first attempt would
be true and would omit the attempt that did the work. Point it at the
episode directory or any segment; the receipt is the same. `--run`
narrows it to one file's latest run.

Receipts are durable: verification re-derives the body from the segments
and entry counts the receipt itself records, so an episode that is resumed
afterwards does not invalidate a receipt issued before it. The
continuation is reported as a note instead.

A receipt also reports its own weaknesses rather than presenting a clean
sheet: `gaps` (a gate decided with untrusted content in scope and recorded
none: fails verification) and `unexplained_sources` (the gate was stricter
than the journal accounts for: a note).

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
