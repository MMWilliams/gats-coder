# CODEX_PARITY — what "rival Codex" means for LEDGER

Compiled 2026-08-10 from the official Codex docs/changelog (CLI v0.147.0 era).
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
| Secret redaction | GAP | Journals store raw command output blobs. |

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
| AGENTS.md (hierarchy, 32KiB cap) | **PART (v0.3.0)** | Repo-root `AGENTS.md` / `LEDGER.md` / `CLAUDE.md` (first found, 8KB cap) feeds AuthorPatch prompts, chat, and review. Global + nested-dir layering: not yet. |
| config.toml + profiles | **PART (v0.3.0)** | `.ledger/config.json` (test_cmd, planner, profile, slots, max_author_attempts) now actually read by run/chat; flags override. Named profiles/global config: no. |
| Hooks / skills / plugins | SCOPE (for now) | Extensibility layer; revisit after M5. |

## 4. Task lifecycle

| Codex capability | LEDGER status | Notes |
|---|---|---|
| Interactive TUI | PART | `ledger chat` REPL (repo-aware, journaled) but no @file search / mid-turn injection. |
| Headless exec + JSON events | PART | `ledger run` is headless and exit-coded; the journal *is* the JSONL event stream (hash-chained, richer than Codex's). No schema-validated final output yet. |
| Session resume/fork/history | GAP | Episodes are one-shot; graph/L2/L3 persist across runs (that's the resume of *knowledge*), but no conversation resume. |
| Parallel subagents | HAVE | `ledger session`: parallel agents in isolated worktrees pooling one transition graph — Codex subagents don't share a learned model at all. |
| Cloud delegation / automations | SCOPE | |
| Best-of-N attempts | GAP | Slots are the natural mechanism (author N candidate patches, test each); untested as a strategy. |

## 5. Git / GitHub

| Codex capability | LEDGER status | Notes |
|---|---|---|
| Local diffs/commits/branches | HAVE | Worktree per episode, `Commit` action, `ledger/<episode>` branches. |
| `codex review` local diff review | **HAVE (v0.3.0)** | `ledger review` (working tree / --staged / --range), obeys project instructions, optional journaled findings. |
| PR creation / @codex review / auto PR review | GAP | Needs a `gh` push/PR action with `risk="network"` — gates already model it. |

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
3. AGENTS.md — **PART (v0.3.0)** (root-level; hierarchy pending)
4. Headless/CI mode — **PART** (exit codes + journal; no output schema)
5. Session resume/fork — **GAP**
6. MCP — **GAP**
7. Git depth (review ✓, PR ✗) — **PART (v0.3.0)**
8. Cloud delegation — **SCOPE**
9. Test-iterate with evidence — **HAVE** (differentiator)
10. Model controls — **PART**
11. Multimodal/web search — **GAP**
12. Skills/plugins/hooks — **SCOPE**

### Build order after v0.3.0
1. PR flow (`gh` actions, network-gated) — completes the Git story.
2. AGENTS.md hierarchy + global config.
3. Structured output for `run` (`--json`: END record + escalation).
4. Best-of-N via slots (author k candidates, RunSuite arbitrates).
5. OS sandbox for RunSuite subprocesses (Job Objects on Windows).
6. MCP sensors/actions.
