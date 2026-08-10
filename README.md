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
pytest                      # 60 tests
python -m ledger.cli demo   # paper's 100-task suite: SR / optimality / calls / variance
```

## Getting started

```
ledger doctor                       # check git, claude CLI, repo, config
ledger init                         # write .ledger/config.json
ledger run "fix the failing test"   # plan, author a patch, apply, verify, commit
ledger status .ledger/episodes/episode/episode.jsonl
```

`run` needs an LLM backend for the patch-authoring skill and the goal
compiler (`--backend claude` uses the local `claude` CLI). The planner itself
makes zero model calls.

## Talking to it

```
ledger chat                                  # REPL, repo-aware
ledger chat --ask "what does planner.py do?" # one-shot
ledger chat --context ledger/planner.py      # inline specific files
ledger chat --journal chat.jsonl             # hash-chained conversation log
```

Chat never mutates repo state and never consumes planning budget. It shares
the audit surface: journaled turns mean "what did I ask, what did it answer,
and which turn led to that episode" stays answerable.

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
ledger incidents <journal>    # claims the sensors refused to confirm
ledger replay <journal>       # bit-exact planner replay
ledger verify-chain <journal> # tamper-evidence
ledger stats <graph.sqlite>   # nodes, edges, dead ends, layer breakdown
```

`why` reports the decision inputs — Q, N, UCB, world-model layer and
confidence per arm — not a model's after-the-fact explanation of itself.

## Status

**M3 + patch authoring.** Core port (M1), real-repo execution layer (M2:
worktree sandbox, skills, fingerprint-memoized sensors, GATE/EXEC journaling,
lie-detection INCIDENTs), learned/generative layers (M3: journal-mined L2,
pluggable L3 with an Eq. 8 cache that drives planning calls to zero on repeat
episodes, goal compiler), plus the LLM patch-authoring skill and the
multi-agent session layer.

Authored diffs are validated with `git apply --check` before they are allowed
into the queue, so a malformed or hallucinated patch produces a plain
non-effect the planner re-plans around rather than a corrupted worktree.

Milestones (DESIGN.md §13/§14.7): M1 core → M2 executor/sandbox/divergence →
M3 L2/L3/goal compiler → **M4 risk gates** (next) → M5 eval harness → M6–M9
RL roadmap.
