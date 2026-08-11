# CODEX_PARITY — what "rival the frontier agents" means for LEDGER

Compiled 2026-08-10 from the official Codex docs/changelog (CLI v0.147.0
era). Extended the same day with studies of **Claude Code** (v2.1.219-era
docs) and **Kimi** (K3 / K2.7-Code / Kimi Code CLI v0.34-era docs) — their
sections and the combined scorecard are at the end of this file.
Status column: **HAVE** (shipped in LEDGER), **PART** (partial),
**GAP** (missing, worth building), **DESIGN** (LEDGER deliberately does it
differently — the audit-first architecture is the product), **SCOPE**
(cloud/enterprise infrastructure; out of scope for a local-first tool).

LEDGER's wedge is what Codex does *not* have: a deterministic planner that
never lets the LLM choose the next action, sensors that refuse to believe
model claims, a hash-chained journal, bit-exact replay, and lie-detection
incidents. Parity work must never trade those away — every capability below
is adopted *inside* that architecture or consciously skipped.

## 1. Execution model

| Codex capability | LEDGER status | Notes |
|---|---|---|
| Arbitrary sandboxed shell | DESIGN | LEDGER runs only catalog actions (RunSuite/AuthorPatch/ApplyPatch/Commit) in a git worktree. Arbitrary shell is the thing the audit story forbids; new powers arrive as *contracted actions*, not a shell. |
| apply_patch structured edits | HAVE | `AuthorPatch` → `git apply --check` → queue; sensors confirm. Stricter than Codex: a diff the repo rejects never becomes state. |
| OS sandbox (Seatbelt/bwrap/Windows) | GAP | Worktree isolation only. OS-level enforcement (Job Objects / bwrap) is the real gap; risk classes + gates are the policy layer it would plug into. |
| Network off by default | HAVE | No action in the catalog has network risk today; anything that does gets `risk="network"`, gated under attended/supervised profiles. |
| Secret redaction | **HAVE (v0.4.0)** | Blobs and suite evidence scrubbed (bearer/API tokens, GitHub/AWS keys, private-key blocks) before content-addressing. |

## 2. Approval / autonomy modes

| Codex capability | LEDGER status | Notes |
|---|---|---|
| Read-only / Agent / Full-access presets | **HAVE (v0.3.0)** | Profiles `attended` (gate write+), `supervised` (network+irreversible), `autonomous` (irreversible only), per DESIGN §7.4. `--profile`, config default. |
| approval_policy axis (untrusted/on-request/never) | **HAVE (v0.3.0)** | The gate asks per action *family* once per episode; `--yes` pre-approves; non-TTY denies by default (fail closed). |
| Denials are survivable | **HAVE (v0.3.0)** | A "no" is journaled (GATE approved=false), observed as a non-effect, and the planner routes around it — Codex just stops. |
| Dangerous-command classifier | DESIGN | LEDGER doesn't classify free-form commands because there are none; risk is declared per action contract and gated symbolically. |
| Auto-review guardian agent | GAP | An LLM adjudicating approvals is un-audit-first; a *policy file* (execpolicy analog) over action names/risks would fit. |
| Enterprise managed config | SCOPE | |

## 3. Instructions / config

| Codex capability | LEDGER status | Notes |
|---|---|---|
| AGENTS.md (hierarchy, 32KiB cap) | **HAVE (v0.4.0)** | Global `~/.ledger/AGENTS.md` + repo root, concatenated global→local (closer wins), 8KB cap; feeds AuthorPatch, chat, review. Nested-dir layering: not yet (episodes are whole-repo). |
| config.toml + profiles | **PART (v0.3.0)** | `.ledger/config.json` (test_cmd, planner, profile, slots, max_author_attempts) now actually read by run/chat; flags override. Named profiles/global config: no. |
| Hooks / skills / plugins | SCOPE (for now) | Extensibility layer; revisit after M5. |

## 4. Task lifecycle

| Codex capability | LEDGER status | Notes |
|---|---|---|
| Interactive TUI | PART | `ledger chat` REPL (repo-aware, journaled) but no @file search / mid-turn injection. |
| Headless exec + JSON events | **HAVE (v0.4.0)** | `ledger run --json` prints one structured result object (goal, plan, incidents, denials, escalation); the hash-chained journal is the event stream. |
| Session resume/fork/history | GAP | Episodes are one-shot; graph/L2/L3 persist across runs (that's the resume of *knowledge*), but no conversation resume. |
| Parallel subagents | HAVE | `ledger session`: parallel agents in isolated worktrees pooling one transition graph — Codex subagents don't share a learned model at all. |
| Cloud delegation / automations | SCOPE | |
| Best-of-N attempts | GAP | Slots are the natural mechanism (author N candidate patches, test each); untested as a strategy. |

## 5. Git / GitHub

| Codex capability | LEDGER status | Notes |
|---|---|---|
| Local diffs/commits/branches | HAVE | Worktree per episode, `Commit` action, `ledger/<episode>` branches. |
| `codex review` local diff review | **HAVE (v0.3.0)** | `ledger review` (working tree / --staged / --range), obeys project instructions, optional journaled findings. |
| PR creation / @codex review / auto PR review | **PART (v0.4.0)** | `--push` adds a network-gated `PushBranch` action: reachable only from a committed worktree, confirmed by `ls-remote` (never the exit code), denied-by-default under supervised/attended. PR creation (`gh pr create`) still pending. |

## 6. Context handling

| Codex capability | LEDGER status | Notes |
|---|---|---|
| On-demand repo exploration | **HAVE (v0.3.0)** | Deterministic `select_context`: failure-evidence paths dominate, then identifier hits — no embeddings, replayable, same inputs → same files. |
| Test-failure evidence in prompts | **HAVE (v0.3.0)** | RunSuite deposits the failing tail; AuthorPatch patches against real output, not vibes. |
| Image input / web search / voice | GAP/SCOPE | Backend-dependent; `claude -p` could pass images later. |

## 7. MCP / extensibility

| Codex capability | LEDGER status | Notes |
|---|---|---|
| MCP client / server / SDK | GAP | Backend protocol is pluggable (`LLMBackend`); MCP would arrive as new *sensors* and *actions*, never a free tool loop. |

## 8. Model / reasoning controls

| Codex capability | LEDGER status | Notes |
|---|---|---|
| Model picker / effort dial | PART | `--model` passes through to `claude`; no effort dial, no alt providers yet (protocol supports them). |

## 9. Testing / verification

| Codex capability | LEDGER status | Notes |
|---|---|---|
| Run tests, iterate until green | HAVE | The planner *is* this loop, with a learned model of it. |
| Cited terminal evidence | HAVE+ | Content-addressed blobs, hash-chained journal, `ledger why` UCB tables, bit-exact replay — stronger than citations. |
| Screenshot/browser verification | SCOPE | |

## Table-stakes scorecard (Codex-ranked)

1. OS-enforced sandbox — **GAP** (worktree only; top remaining gap)
2. Graduated autonomy — **HAVE (v0.3.0)**
3. AGENTS.md — **HAVE (v0.4.0)** (global + root; nested-dir pending)
4. Headless/CI mode — **HAVE (v0.4.0)** (`--json` + exit codes + journal)
5. Session resume/fork — **GAP**
6. MCP — **GAP**
7. Git depth (review ✓, push ✓, PR ✗) — **PART (v0.4.0)**
8. Cloud delegation — **SCOPE**
9. Test-iterate with evidence — **HAVE** (differentiator)
10. Model controls — **PART**
11. Multimodal/web search — **GAP**
12. Skills/plugins/hooks — **SCOPE**

### Build order after v0.4.0 — status as of v0.5.0
1. ~~PR creation~~ — **SHIPPED v0.5.0** (`--pr`: CreatePR via `gh`,
   network-gated, `gh pr view`-confirmed sensor).
2. ~~Best-of-N~~ — **SHIPPED v0.5.0** (`--attempts N`: parallel isolated
   attempts via the session layer, deterministic winner pick).
3. OS sandbox for RunSuite subprocesses — still open.
4. ~~Episode resume~~ — **SHIPPED v0.5.0** (`--resume`: worktree reuse,
   warm graph, journal segments that verify independently).
5. MCP sensors/actions — still open.

---

# Claude Code study (docs v2.1.219-era)

| Claude Code capability | LEDGER status | Notes |
|---|---|---|
| Permission rules (allow/deny/ask) + modes | **HAVE (v0.5.0)** | Ordered policy rules with `ask` decisions and journaled reasons; scope ladder: `--deny`/`--allow` flags (invocation) → config (project). Deny outranks `--yes`. |
| Hooks (PreToolUse/PostToolUse …) | **HAVE (v0.5.0)** | `pre_action` (gate-capable: nonzero exit blocks, fail closed) + `post_action`, matched by action family, journaled as HOOK entries with output blobs. Command hooks only — HTTP/MCP/prompt hook types not yet. |
| CLAUDE.md hierarchy + imports | HAVE | AGENTS.md/LEDGER.md/CLAUDE.md, global `~/.ledger` + repo root layering. Nested-dir + imports: open. |
| Auto memory (agent-written) | DESIGN | LEDGER's memory is *mined from the journal* (L2), never self-narrated — learned dynamics instead of learned prose. |
| OS sandbox (FS/network for Bash) | GAP | Top remaining gap, all three rivals have some form. |
| Headless `-p` + json/stream-json | HAVE | `--json` result object; the hash-chained journal *is* the stream (tail the .jsonl). |
| Session resume/fork/checkpoints | PART | Episode `--resume` shipped; conversation forking N/A by design (the planner has no conversation); worktree+git = code checkpoints. |
| Subagents (isolated context, per-agent model) | **HAVE (v0.5.0)** | Session agents now take per-agent backends (`"model"` in the spec). |
| Dynamic workflows (script orchestration) | DESIGN | The deterministic planner + session fan-out is LEDGER's orchestration; a script layer would just be a second planner. |
| Skills / plugins / marketplaces | SCOPE | |
| MCP client/server | GAP | |
| GitHub Action + @claude PR review | GAP | `--json` + exit codes make CI wiring trivial; the action wrapper is packaging work. |
| Checkpoint/rewind (Esc-Esc) | DESIGN | Episode branches are disposable; `git reset` on the episode branch is the rewind. |
| /doctor with config validation | **HAVE (v0.5.0)** | doctor lints config.json: unknown keys, bad profile, malformed hooks/policy. |

# Kimi study (K3 / K2.7-Code / Kimi Code CLI v0.34-era)

Naming note: there is no product literally called "Kimi K3 Coder" — the
study covers Kimi K3 (open-weight flagship), K2.7-Code (coding model), and
Kimi Code CLI (the agent).

| Kimi capability | LEDGER status | Notes |
|---|---|---|
| Scoped permission rules with `reason` audit field | **HAVE (v0.5.0)** | First-match ordered rules; the firing rule's reason lands in the GATE journal entry. |
| Hooks as gates (PreToolUse …) | **HAVE (v0.5.0)** | Same engine as above. |
| `[loop_control]` step/attempt caps | HAVE | Planner max_steps + author budget → escalation. |
| AGENTS.md + kimi.md memory hierarchy | HAVE | Layered instructions; journal-mined L2 as memory. |
| Secondary-model economics (per-subagent models) | **HAVE (v0.5.0)** | `"model"` per session agent spec. |
| Agent Swarm / PARL (RL-trained orchestrator) | ROADMAP | LEDGER's §14 RL roadmap trains *skills* under sensor rewards; orchestration stays deterministic on purpose. |
| WebBridge (user's own browser via CDP) / Computer Use plugin | SCOPE | Not expressible as verifiable contracts yet. |
| Native video input in the coding loop | SCOPE | Backend-dependent. |
| Conversational MCP setup, plugin marketplace | GAP/SCOPE | With MCP. |
| Session export / `kimi vis` run visualizer | **HAVE (v0.6.0)** | `ledger vis`: any journal → one self-contained HTML report (timeline, decision tables, state diffs, gate/hook/incident/escalation markers). |
| Open weights / protocol-agnostic backends | PART | `LLMBackend` protocol is model-agnostic; only the claude CLI backend is implemented — an OpenAI-compatible/local backend is a small add. |
| doctor config validation | **HAVE (v0.5.0)** | |

# Combined table-stakes (union of all three studies)

1. OS-enforced sandbox — **GAP** (the one every rival has in some form)
2. Graduated autonomy + policy rules + hooks — **HAVE (v0.5.0)**
3. Layered instruction files — **HAVE**
4. Headless CI (`--json`, exit codes, event stream) — **HAVE**
5. Session/episode resume — **HAVE (v0.5.0)** (episode-level)
6. MCP — **GAP**
7. Git depth (review ✓ push ✓ PR ✓; @mention auto-review ✗) — **HAVE-** 
8. Parallel subagents w/ per-agent models — **HAVE (v0.5.0)**
9. Test-iterate with hard evidence — **HAVE+** (differentiator)
10. Model controls (picker ✓; effort dial ✗; alt providers ✗) — **PART**
11. Best-of-N — **HAVE (v0.5.0)**
12. Extensibility (hooks ✓; skills/plugins ✗) — **PART**

### Build order after v0.5.0 — status as of v0.6.0
1. OS sandbox for skill subprocesses (Job Objects / bwrap) — still open.
2. OpenAI-compatible + local (Ollama) backends via `LLMBackend` — open.
3. MCP client surfaced as contracted sensors/actions — open.
4. GitHub Action wrapper (`ledger run --json` in CI) + PR auto-review — open.
5. ~~`ledger vis`~~ — **SHIPPED v0.6.0**, plus full terminal styling
   (verdict headlines, colored plan chains, aligned UCB tables,
   escalation-aware next-step hints; plain off-TTY, NO_COLOR honored).
6. HTTP hook type; nested-dir instruction files — open.
