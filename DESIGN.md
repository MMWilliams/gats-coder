# LEDGER — Design Doc
## A GATS-Planned, Audit-First Autonomous Coding Agent

**Author:** Maureese Williams
**Date:** 2026-07-14
**Status:** Draft v1
**Framework:** GATS — Graph-Augmented Tree Search with Layered World Models (arXiv:2607.08894)
**Reference implementations:**
- Core library: `C:\Users\reese\gats\gats2\src` (`core/world_model.py`, `core/mcts.py`, `core/action_model.py`, `core/types.py`, `agents/gats2.py`)
- Paper artifacts + lie-detection demos: `C:\Users\reese\gats_clean\gats2` (`demos/agent_lies_demo.py`, `demos/bench_lie_detection.py`)

---

## 1. Summary

LEDGER is an autonomous coding agent whose **plans are computed by GATS, not by an LLM**.
The LLM writes code; it never decides what happens next. Every action the agent takes is
selected by deterministic UCB1 search over a persistent state-transition graph
(paper §3.4–3.5), every state transition is predicted by the layered world model
(L1 symbolic / L2 learned / L3 LLM, §3.2), and every decision — including the
alternatives that were *not* chosen — is recorded in an append-only, hash-chained
journal that supports bit-exact replay.

This inverts the architecture of ReAct/LATS-style coding agents (Claude Code, Devin,
OpenHands), where the LLM is the planner and the audit trail is a transcript of
stochastic samples. GATS's results motivate the inversion directly:

| Property (from paper) | LATS | ReAct | GATS |
|---|---|---|---|
| Stress-test success (120 tasks, incl. `coding_task`) | 88.9% | 23.9% | **100%** |
| `coding_task` category | 63.3% | 0.0% | **100%** |
| LLM calls per task during planning | ~37 | ~13 | **0** |
| Plan variance across seeds | 1–5% | 5% | **0%** |

Zero plan variance is the property that makes real auditability possible: a plan that
can be replayed deterministically can be *verified*, not merely *narrated*.

**Two design pillars:**

1. **Highly agentic.** LEDGER runs long-horizon episodes unattended: it compiles a
   natural-language task into goal propositions, plans, executes, verifies, recovers
   from divergence, and replans — pausing for a human only at risk gates it can prove
   it cannot pass alone.
2. **Auditable by construction.** The symbolic state is the ground truth. LLM output
   never enters the state; only **sensor-verified propositions** do. Any gap between
   what the model *claims* and what the sensors *observe* is a first-class incident —
   the production generalization of `demos/agent_lies_demo.py`.

### Non-goals (v1)

- Not a general computer-use agent. Scope: git repos, build/test toolchains, package
  managers, local file edits, PR creation.
- Not multi-repo or multi-agent. One episode = one repo = one goal.
- No learned value networks. Paper §6 flags BFS scalability beyond |A|≈20, d>10; v1
  stays inside that envelope by construction (see §5.2) and defers learned heuristics.

---

## 2. Why GATS fits a coding agent

Coding is the domain the paper's stress test says LLM-guided planners are worst at:
`coding_task` (sequential development with ordering traps — main-before-functions,
missing imports) is where LATS drops to 63.3% and ReAct to 0%. The structural reasons
map one-to-one onto real software work:

- **Sequential dependencies** — can't run tests before writing them; can't commit
  before staging. (Paper: "Actions unlock subsequent actions.")
- **Irreversible dead-ends** — force-push over history, dropped migrations, published
  packages. (Paper: `no_backtrack`, `commitment_cascade`.)
- **Deceptive shortcuts** — skipping the failing test to "save time," hard-coding a
  fixture. (Paper: `deceptive`, `trap_heavy`.)
- **Resource constraints** — CI minutes, API rate limits, context budget.
  (Paper: `resource_puzzle`, `memory_limit`.)

And crucially, coding is a domain where **action semantics can be formalized** — the
exact condition paper §6 (Comparison Fairness) identifies as GATS's home turf:
"GATS is optimized for domains where action semantics can be formalized (APIs, tool
use, structured environments)." `git commit`, `pytest`, `cargo build` have contracts
we can write down. That is L1 coverage, and L1 coverage is what drives LLM calls
during planning to zero (Eq. 9).

---

## 3. Architecture

```
                        ┌────────────────────────────────────────────┐
                        │                 ORCHESTRATOR                │
                        │        (episode loop = paper Alg. 2)        │
                        └──┬─────────┬───────────┬─────────┬─────────┘
                           │         │           │         │
              ┌────────────▼──┐ ┌────▼─────┐ ┌───▼──────┐ ┌▼─────────────┐
              │ GOAL COMPILER │ │ PLANNER  │ │ EXECUTOR │ │ RISK GATE    │
              │ (LLM, audited)│ │ (GATS,   │ │ (skills: │ │ (policy      │
              │ NL task → G   │ │ Alg. 1,  │ │ LLM-     │ │ engine +     │
              │ + acceptance  │ │ UCB1 over│ │ synth in │ │ human        │
              │ sensors       │ │ graph 𝒢) │ │ sandbox) │ │ escalation)  │
              └───────────────┘ └────┬─────┘ └───┬──────┘ └──────────────┘
                                     │           │
                        ┌────────────▼───┐  ┌────▼────────────┐
                        │ LAYERED WORLD  │  │  SENSOR BANK    │
                        │ MODEL  W(s,a)  │  │  (deterministic │
                        │ L1 contracts   │  │  probes → props)│
                        │ L2 repo stats  │  └────┬────────────┘
                        │ L3 LLM (cached)│       │
                        └────────────────┘       │
                                     ┌───────────▼───────────────────┐
                                     │  THE LEDGER                   │
                                     │  hash-chained journal (JSONL) │
                                     │  + graph store (SQLite)       │
                                     │  + replay/audit CLI           │
                                     └───────────────────────────────┘
```

**Division of labor — the load-bearing decision:**

| Concern | Owner | Stochastic? |
|---|---|---|
| *What to do next* | GATS planner (UCB1, Eq. 14) | No — deterministic, 0 variance |
| *What an action will do* | Layered world model W(s,a) | L1/L2 no; L3 cached-once |
| *How to do it* (write the diff, the command) | LLM skill inside the action | Yes — but confined & verified |
| *What actually happened* | Sensor bank | No — deterministic probes |
| *Whether it's safe* | Risk gate over graph reachability | No |

Stochasticity exists only *inside* action execution, never in plan selection. The
planner's view of an action is its contract; the sensors decide whether the contract
was honored.

---

## 4. State: the repo as a proposition set

Paper §3.1: states are sets of propositions; `k(s)` canonicalizes them so the graph
merges paths that reach the same state (Eq. 12 — the transposition-table property).

### 4.1 Proposition vocabulary

Propositions are produced **only by sensors** — cheap deterministic probes. Sample
vocabulary (extensible per-repo via config):

```
repo.clean                     # git status --porcelain empty
repo.branch:<name>
repo.head:<short-sha>
build.ok                       # exit code of configured build cmd
tests.pass:<suite>             # unit / integration / e2e, per-suite
tests.fail_count:<bucket>      # 0 / 1-5 / 6+  (bucketed to keep |S| finite)
lint.ok   typecheck.ok
dep.installed:<pkg>
file.exists:<path-hash>
patch.applied:<patch-id>       # content-addressed diff id
test.written:<goal-facet>      # acceptance test for a goal facet exists
goal.facet:<id>                # emitted by acceptance sensors (see §7.1)
recoverable                    # no irreversible op has occurred (see §8.2)
budget.llm:<bucket>  budget.ci:<bucket>
```

`k(s)` = SHA-256 over the sorted proposition set. Continuous values are bucketed —
the abstraction must keep the reachable state space finite and mergeable, or graph
reuse (the paper's core amortization argument, §3.3) evaporates.

### 4.2 Sensor bank

- Each sensor: pure function `probe(repo) → {propositions}` with a declared cost tier
  (instant: `git status`; cheap: `lint`; expensive: full suite).
- Expensive sensors are memoized on `(HEAD sha, dirty-diff hash, suite id)` and only
  re-run when an action's effect set touches their inputs.
- Sensors are versioned; the sensor-set hash is stamped into every journal entry so a
  replay can detect "the world changed because the instruments changed."

### 4.3 Abstraction leakage

Two genuinely different repo states can share one `k(s)` (the abstraction is lossy by
design). This is safe because plans are *re-verified against reality after every
action* (§6.3): leakage can waste search effort but cannot produce silent wrong
beliefs — divergence detection catches it and refines the vocabulary (a new
discriminating proposition can be added; graph nodes re-key lazily).

---

## 5. Actions: two altitudes

### 5.1 Plan-level actions (what GATS searches over)

STRIPS-style contracts, exactly the paper's `prec / add / del` form (§3.1), extended
with cost and risk class:

```yaml
action: ImplementPatch
params: {facet: goal.facet-id}
prec:   [test.written:<facet>, "!tests.pass:unit", recoverable]
add:    [patch.applied:<new-id>]
del:    [repo.clean]
risk:   write          # read | write | exec | network | irreversible
cost:   {llm_calls: ~1-3, wall: minutes}
skill:  llm.synthesize_diff        # §5.2

action: RunSuite
params: {suite: unit}
prec:   [build.ok]
add:    [tests.pass:unit | tests.fail_count:*]   # stochastic effect → L2 (§6.2)
del:    []
risk:   exec
skill:  shell.pytest

action: ForcePush           # exists so the planner can PROVE it shouldn't use it
prec:   [repo.branch:<b>, "!branch.protected:<b>"]
add:    [remote.synced]
del:    [recoverable]       # deleting `recoverable` → dead-end machinery (§8.2)
risk:   irreversible
```

The v1 catalog is ~16 actions: `WriteAcceptanceTest`, `ImplementPatch`, `RunSuite(×3)`,
`Build`, `Lint`, `Typecheck`, `InstallDep`, `ReadContext`, `Refactor`, `Revert`,
`Commit`, `PushBranch`, `OpenPR`, `AskHuman`, `ForcePush`. This keeps
|A_app(s)| ≤ ~20 at any state — inside the BFS-value-estimation envelope the paper
validates (§6 Scalability: |A|=15, d=20, ≤500 BFS states) — so `StateValue` (Eq. 18,
α/(d_G+1)) works unmodified.

### 5.2 Skill-level execution (where the LLM lives)

Inside an action, a **skill** does the actual work — an LLM synthesizes the diff, the
command, the test. The planner never sees tokens. From the planner's perspective,
`ImplementPatch` either achieved `patch.applied ∧ build.ok` or it didn't.

- Skills run in a sandbox (worktree copy; network allowlist by risk class).
- A skill's output is **proposed**, not believed: sensors re-probe and produce the
  observed state. The skill *claiming* "I fixed the test" is journaled as a claim;
  only `tests.pass:unit` from the sensor enters `s`.
- Skill LLM calls are *execution* cost, not *planning* cost — the paper's
  "zero LLM calls per task during planning" claim maps to LEDGER as: **zero LLM calls
  to decide the next action**, budgeted-and-metered LLM calls to perform it. The
  journal reports both counters separately so the claim stays honest.

Growth path: new tools enter as L3-predicted novel actions (§6.2), get contracts
promoted to L1 once their observed effect distribution stabilizes in L2.

---

## 6. The layered world model, mapped to code

Port of `src/core/world_model.py`, with the layer-selection rule of Eq. 5
(accept layer i when p_i ≥ τ_i) and the cost ordering c1 ≪ c2 ≪ c3 (Eq. 4).

### 6.1 L1 — Symbolic contracts (target: ≥60% of transitions)

Hand-written contracts for the v1 catalog (§5.1). Deterministic actions
(`Commit`, `InstallDep`, `Lint` on clean input) resolve here at p=1.0, cost ≈ 0.
Paper Table 4 projects ~60% L1 coverage open-domain; the curated catalog should beat
that because coding tools have unusually crisp semantics.

### 6.2 L2 — Learned repo statistics (target: ~30%)

Transition counts mined from the journal, per-repo first, global as prior. Confidence
`p₂ = min(n(a)/n₀, 1)` with n₀=10 (Eq. 6). This is where coding's *inherent*
stochasticity lives and becomes measurable instead of mystical:

- `RunSuite` after `ImplementPatch` in repo X: `{pass: 0.71, fail_count:1-5: 0.29}` —
  the planner's value backup uses `v = p·StateValue(s′) − λ(1−p)` (Eq. 17), so
  low-confidence transitions are *penalized in search*, which naturally makes the
  planner prefer verified paths (write test → small patch → run) over YOLO paths
  (big patch → hope).
- **Flaky tests get detected, not endured:** an action whose observed effects stay
  bimodal after n₀ observations (confidence saturates but entropy stays high) flags
  `flaky:<suite>` — surfaced in the audit report and usable as a goal facet itself.

### 6.3 L3 — LLM effect prediction (target: ≤10%, cached)

For novel actions (unfamiliar build system, new CLI tool): the LLM predicts
`(s′, 0.5)` per Eq. 3, cached on `(k(s), action-signature)` per Eq. 8, so the count
of LLM planning calls is bounded by *distinct novel transitions* (Eq. 9) — one-time
bootstrapping, ~50 calls in the paper, amortized forever after.

### 6.4 Divergence: verify-after-execute

The paper's planner assumes predictions hold; a real repo will disagree sometimes.
After every execution:

```
s′_pred  = W(s, a)          # what the model said
s′_obs   = SensorBank(repo) # what the world says
if k(s′_obs) != k(s′_pred):
    journal.emit(DIVERGENCE, pred=…, obs=…, layer=…, p=…)
    L2.update(s, a, s′_obs)                 # the world model learns
    graph.invalidate_downstream(k(s′_pred)) # stale values don't leak (see below)
    replan_from(k(s′_obs))                  # Alg. 2 continues from reality
```

`invalidate_downstream` recomputes `StateValue` for affected nodes and decays their
Q toward the fresh estimate — visit counts N are kept (exploration history is still
real) but values must not launder a falsified prediction. **Divergence rate per layer
is a top-line health metric**: L1 divergence means a contract is wrong (bug in our
spec — page the operator); L2 divergence is expected and is the learning signal.

---

## 7. Agentic operation

### 7.1 Goal compilation (the one up-front LLM planning-adjacent step)

The LLM translates a natural-language task into a **goal contract**:

- goal propositions `G` (e.g., `tests.pass:unit ∧ goal.facet:endpoint-returns-404 ∧
  lint.ok ∧ repo.clean`),
- **acceptance sensors** — for each facet, a concrete, executable check (usually a
  generated test) whose passing emits `goal.facet:<id>`.

The goal contract is a journaled, human-readable artifact. On `attended` profile it
is shown for approval before the episode starts; on `autonomous` it is executed and
auditable after the fact. This is deliberately the *only* place LLM judgment shapes
the objective — and it is pinned down as testable propositions before any planning
happens, so "the agent quietly redefined success" is structurally impossible: success
is whatever the frozen acceptance sensors say, and changing them mid-episode is a
journaled `GOAL_AMENDED` event that (on any profile) requires human sign-off.

### 7.2 Episode loop (paper Alg. 2, verbatim + divergence arm)

```
compile goal → G, freeze acceptance sensors
s ← SensorBank(repo);  𝒢 ← load persistent graph for this repo
while G ⊄ s and steps < max_steps:
    A_app ← applicable actions (prec ⊆ s)
    if A_app = ∅: escalate(AskHuman, reason=STUCK)
    a* ← GATSSearch(s, A_app, 𝒢, b=10, c=1.0)     # Alg. 1; zero LLM calls
    if risk(a*) requires gate: gate(a*)             # §8
    execute skill(a*) in sandbox
    s′_obs ← SensorBank(repo);  verify/diverge      # §6.4
    journal.emit(STEP, …)                           # §9
    s ← s′_obs
finalize: Commit → PushBranch → OpenPR with audit report attached
```

Budget b=10 and c=1.0 are the paper's validated defaults (Table 2 shows saturation at
b=10; Table 7 shows insensitivity to c). Both are config, and both are journaled.

### 7.3 Persistence = compounding skill

The graph 𝒢 is per-repo and **survives across episodes** (paper §3.3: persistence
amortizes prediction, BFS value estimation, and UCB statistics). Concretely: the
second task in a repo starts with the transition structure, flake rates, and
dead-end map the first task paid for. L2 stats make the agent *measurably better at
this specific codebase over time* — and the journal can prove it (divergence rate
and L3-call count should fall monotonically per repo).

### 7.4 Escalation policy

`AskHuman` is a real action in the catalog with cost high enough that UCB only
selects it when alternatives are exhausted or gated. Triggers:

- `A_app = ∅` (planner provably stuck),
- all paths to G pass through an `irreversible` action (§8.2),
- divergence storm (≥3 consecutive L1 divergences → instruments are broken),
- budget exhaustion (`budget.llm:empty` precondition blocks LLM skills).

Autonomy profiles: `attended` (gate on write+), `supervised` (gate on
irreversible+network; default), `autonomous` (gate on irreversible only).

---

## 8. Safety via graph semantics

### 8.1 Risk classes gate execution, not search

The planner may *search through* a gated action (it needs to, to value it); the gate
fires at *execution*. UCB with the λ-penalty (Eq. 17) plus gate cost keeps risky
paths naturally deprioritized when safe paths exist.

### 8.2 Irreversibility = deleting `recoverable`

Every irreversible action deletes the `recoverable` proposition. Consequences fall
out of existing GATS machinery, no new mechanism needed:

- Any state lacking `recoverable` from which G is unreachable is a **dead end** the
  graph marks and remembers (§3.3, dead-end detection) — permanently, across
  episodes. The agent that once got burned by a bad rebase *cannot forget it*.
- The risk gate's question is answerable *symbolically*: "does a path to G exist that
  keeps `recoverable`?" If yes → take it (no human needed). If no → escalate with the
  proof (the exhausted subgraph) attached. This is the paper's `no_backtrack` /
  `commitment_cascade` result (100% vs ReAct's 0%) operationalized as a safety
  property.

### 8.3 Sandbox

Skills execute in a git worktree with network egress off by default (allowlisted per
risk class). The real branch is only touched by `Commit`/`Push` actions, which are
themselves plan-level, gated, and journaled.

---

## 9. Auditability: the Ledger

The reason to build on GATS rather than bolt logging onto an LLM planner: **an
auditor can distinguish "why the agent did X" from "the story the agent tells about
X."** With LATS-style planning those are the same artifact (sampled text). Here they
are different artifacts, and the first one is deterministic.

### 9.1 Journal: append-only, hash-chained

JSONL; every entry carries `prev_hash = SHA-256(prev_entry)` — tamper-evident, and
each entry stamps versions of: agent, action catalog, sensor set, world model, seed.

Entry types (per step):

```jsonc
{ "t": "PLAN",
  "state": "k(s)…",
  "ucb_table": [ {"a":"ImplementPatch","Q":0.62,"N":4,"ucb":1.31},
                 {"a":"RunSuite","Q":0.55,"N":3,"ucb":1.29}, … ],   // ALL arms
  "chosen": "ImplementPatch",
  "wm": {"layer":"L1","p":1.0,"pred_state":"k(s')…"},
  "budget_spent": {"search_iters":10,"llm_planning_calls":0} }

{ "t": "EXEC", "skill":"llm.synthesize_diff",
  "llm_claims": ["patch fixes the 404 handler"],       // recorded, NOT believed
  "artifacts": {"diff":"sha256:…", "cmd_log":"sha256:…"},
  "sandbox": {"worktree":"…","net":"off"} }

{ "t": "OBSERVE", "obs_state":"k(s')…", "sensors_run":[…],
  "diverged": false }

{ "t": "DIVERGENCE" | "INCIDENT" | "GATE" | "GOAL_AMENDED" | … }
```

Content-addressed blobs (diffs, tool output) live beside the journal; the chain
covers their hashes.

### 9.2 Deterministic replay (the killer feature)

Because GATS plans have **zero variance** (Table 1) and the graph + seed + versions
are journaled, `ledger replay <episode>` re-runs the *planner* (not the tools) over
the recorded observations and must reproduce the identical action sequence and UCB
tables. Any mismatch means tampering, version drift, or a bug — all of which you
want to know about. No LLM-planned agent can offer this: replaying a sampled
trajectory is re-rolling dice.

### 9.3 Why-trace, not post-hoc rationalization

"Why did the agent refactor before fixing the bug?" is answered from the recorded
UCB table at that step: the Q/N values, the world-model layer and confidence behind
each arm, and the StateValue estimates — the *actual* decision inputs (Eq. 14), not
an LLM's after-the-fact essay. `ledger why <step>` renders this as prose with the
numbers attached.

### 9.4 Lie detection as a standing invariant

Generalizing `demos/agent_lies_demo.py`: every `EXEC` entry records the skill LLM's
claims; every `OBSERVE` entry records sensor truth. A standing checker diffs them:

- claim "tests pass" ∧ sensor `tests.fail_count:1-5` → `INCIDENT{type: false_claim}`,
- claim "no files outside scope touched" ∧ worktree diff says otherwise → incident.

Incidents never silently correct — they halt-or-gate per profile and are permanent
journal entries. The symbolic layer is the ground truth; the LLM is a fluent but
untrusted contractor. (This is the DEF CON "When the Agent Lies" thesis as an
architectural invariant rather than a demo.)

### 9.5 Audit surfaces

- **Plan certificate** (pre-execution, on gated actions): predicted path to G with
  per-edge provenance — layer, confidence, visit counts.
- **Episode report** (post-execution, attached to the PR): goal contract, action
  sequence, divergences, incidents, LLM call accounting split
  planning (should be 0) / goal-compilation / skills, flake findings.
- **`ledger` CLI:** `replay`, `why <step>`, `verify-chain`, `graph export` (DOT/JSON
  of 𝒢 with dead-ends marked), `stats` (per-repo L1/L2/L3 hit rates vs the paper's
  Table 4 projections: ~60/30/10).

---

## 10. Data model & storage

Per-repo directory `.ledger/`:

| Store | Tech | Contents |
|---|---|---|
| `journal/*.jsonl` | append-only JSONL + hash chain | everything in §9.1 |
| `graph.sqlite` | SQLite | nodes `(k, props, dead_end, StateValue)`; edges `(k, a, k′, p, layer, N, Q)` — schema mirrors `core/types.py` |
| `wm/l2_stats.sqlite` | SQLite | `(action_sig, effect_set, count)` per Eq. 2/6 |
| `wm/l3_cache.sqlite` | SQLite | `(k(s), action_sig) → (s′, p)` per Eq. 8 |
| `blobs/` | content-addressed files | diffs, tool logs, goal contracts |
| `catalog.yaml`, `sensors.yaml` | config | action contracts, sensor defs (versioned, hashed) |

Python 3.10+, porting from `gats\gats2\src`: `core/world_model.py` (layer engine),
`core/mcts.py` (UCB1 search → refit to explicit graph edges per Alg. 1),
`core/action_model.py` (STRIPS contracts), `core/uncertainty.py` (Eq. 17 λ-penalty).
LLM skills via Claude API (goal compilation + synthesis; L3 predictions can run on a
local model — they're low-stakes and cached).

---

## 11. Evaluation plan

Reuse the paper's harness pattern (`experiments/run_stress_test.py`) with categories
mapped to real coding equivalents:

| Paper category | LEDGER eval scenario |
|---|---|
| `coding_task` | multi-file feature with ordering traps (import before use, migration before model) |
| `no_backtrack` | task where an available destructive git op would lose work |
| `commitment_cascade` | dependency/framework choice that locks later options |
| `resource_puzzle` | rate-limited API + CI-minute budget |
| `deceptive` | hard-codeable test fixture (the shortcut must be search-rejected, not prompt-rejected) |
| `very_long_horizon` | 12+ step refactor with periodic breakage |

**Metrics** (superset of paper §4.3): success rate on acceptance sensors; optimality
(plan length vs known-optimal); **LLM planning calls (must be 0)** and skill calls;
plan variance across seeds (must be 0); divergence rate by layer; incident
(false-claim) detection rate against seeded lying skills (reuse
`bench_lie_detection.py`); audit completeness (% steps passing `verify-chain` +
`replay`). Baseline: same skills, same sandbox, LLM-as-planner (ReAct loop) — so the
delta isolates the planner, mirroring the paper's matched-budget LATS comparison
(Table 5).

Secondary: SWE-bench-lite subset, expecting the win to come from *reliability and
auditability at fixed cost* rather than raw solve rate (frontier-LLM planners are
strong there; they're just expensive, stochastic, and unauditable).

---

## 12. Risks & open questions

1. **Abstraction granularity.** Too coarse → planner can't distinguish states that
   matter; too fine → graph reuse dies. Mitigation: divergence-driven vocabulary
   refinement (§4.3); track node-merge rate as the health metric.
2. **BFS envelope.** Catalog growth past |A|≈20 breaks `StateValue` (paper §6).
   Mitigation: hierarchical goals (facets planned sequentially keep A_app small);
   learned value fn is the paper's own named future-work item, deferred.
3. **Skill quality ceiling.** GATS guarantees the *right action order*, not a
   *good diff* — if `ImplementPatch` keeps failing its postconditions, the planner
   loops write/test/revert. Mitigation: per-action attempt budgets → the loop
   surfaces as `budget` propositions and forces escalation; skill retries with
   accumulated failure context.
4. **Flaky sensors are worse than flaky tests.** A nondeterministic sensor corrupts
   `k(s)` itself. Sensors must be pure and hermetic; the L1-divergence-storm alarm
   (§7.4) is the tripwire.
5. **Goal compilation is a trust root.** A bad goal contract means faithfully
   auditable pursuit of the wrong target. Mitigation: it's frozen, human-visible,
   and amendments are gated (§7.1) — but v1 should ship `attended`-by-default.
6. **Open question:** cross-repo L2 transfer — do transition stats for
   (`pytest`, `cargo`) priors transfer well enough to warm-start new repos, or does
   per-repo idiosyncrasy dominate?
7. **Found & fixed in M3 — Eq. 17 self-loop starvation.** A deterministic no-op
   (an applicable action whose effects are already present) scores
   `1.0·StateValue(s)` — which already prices in the optimal continuation —
   while the uncertain-but-necessary edge on that continuation scores
   `p·StateValue(s′) − λ(1−p) < StateValue(s)` for p<1. The planner then prefers
   the no-op forever. Invisible in the paper's fully symbolic setting (p=1
   reduces to deterministic backup, §3.4); guaranteed in open domains. Fix:
   prune predicted self-loops from A_app — a predicted no-op cannot lie on a
   shortest path. Corollary: an action *no* world-model layer can predict is
   also pruned; GATS cannot plan through what it cannot model.
8. **Found in M2/M3 — episode-unique propositions block cross-episode L2.**
   `repo.branch:ledger/<episode>` makes every episode's state keys unique, so
   per-state L2 stats never transfer between episodes on the same repo. Needs
   either state-abstraction refinement (strip agent-generated identifiers from
   k(s)) or the context-generalizing L2 of §14.4 (the naive action-global
   argmax of Eq. 2 mispredicts on bimodal effects, e.g. RunSuite pass/fail).

---

## 13. Milestones

| # | Deliverable | Exit criterion |
|---|---|---|
| M1 | Core port: graph store + UCB1 planner + L1 contracts + sensors on a toy repo | paper Alg. 1/2 reproduced on synthetic coding tasks; `replay` bit-exact |
| M2 | Executor + sandbox + divergence loop + journal/hash chain | end-to-end episode on a real small repo (`attended`), `verify-chain` green |
| M3 | L2 mining + L3 cache + goal compiler | second episode on same repo shows falling L3 calls; lie-detection bench ≥ demo parity |
| M4 | Risk gates + dead-end persistence + escalation | `no_backtrack` scenario: agent proves-and-asks instead of force-pushing |
| M5 | Eval harness (§11) + episode reports on PRs | matched-budget win vs ReAct baseline on the mapped stress suite |

---

## 14. Reinforcement Learning Roadmap

Survey basis: the 2024–2026 RLVR-for-code literature (DeepSeek-R1/GRPO, SWE-RL,
SWE-Gym, R2E-Gym, DeepSWE, DeepCoder, DAPO/Dr.GRPO, SWE-Search, S*). The field's
converged recipe is: **verifiable execution rewards + critic-free GRPO-family
optimization + massive sandboxed rollout infrastructure**, with reward hacking,
entropy collapse, and long-horizon credit assignment as the dominant failure modes.

### 14.1 Why LEDGER is unusually RL-ready

The published systems spend most of their engineering on things LEDGER gets for free:

1. **The verifier already exists.** RLVR needs a deterministic reward computer;
   LEDGER's sensor bank *is* one. Binary all-sensors-pass rewards are exactly the
   shape DeepCoder chose specifically to resist reward hacking.
2. **The rollout infra already exists.** Qwen3-Coder ran 20,000 parallel cloud
   environments; DeepSWE ran Kubernetes-orchestrated Docker fleets — the field calls
   this the "rollout infrastructure tax." LEDGER's sandboxed worktrees + hermetic
   sensors (§8.3, §4.2) are that infrastructure, and the journal (§9.1) is the
   trajectory store, already content-addressed and provenance-stamped. Training data
   with a chain-of-custody is a differentiator no published system has.
3. **Credit assignment is solved structurally, not algorithmically.** The open
   problem in multi-turn SWE RL is assigning credit across 50–100 environment steps
   (trajectory-level GRPO, StarPO, etc.). GATS collapses it: the *planner* owns the
   horizon; a skill invocation is a short episode whose reward is its own
   sensor-verified postcondition. Context blowup (trajectories exceeding the window)
   disappears the same way — skills see per-action context, never the whole episode.
4. **Reward hacking defense is an architectural invariant.** Acceptance sensors are
   frozen at goal-compile time (§7.1) and held out from the skill's view; the
   lie-detection layer (§9.4) turns test-gaming into a journaled `INCIDENT` with
   negative reward. The mitigations the field bolts on (held-out test synthesis,
   binary rewards, hidden splits) are load-bearing walls here.

### 14.2 Surface A — the skill policy (diff/test writer): outcome RLVR

This is the direct analog of DeepSWE/SWE-RL training, staged per the field's
consensus ladder (each stage gates the next):

- **Stage 0 — log-and-learn (free, starts at M2).** Journals accumulate
  `(context, generated diff, verified outcome)` triples during normal operation.
- **Stage 1 — rejection-sampling fine-tuning (RFT/STaR).** Sample k diffs per task on
  R2E-Gym-Subset / SWE-Gym / SWE-smith + own journals; keep postcondition-passing
  ones; SFT (LoRA on a 7B–14B strong distilled base — "pure RL" wins all started
  from strong bases). Local dual-5090 viable. Precedent: SWE-Gym self-improvement
  hit 19.7% SWE-bench Lite (32B) with RFT alone. *Gate: move on when new-solved-task
  rate plateaus.*
- **Stage 2 — GRPO++ (the DeepSWE recipe, not PPO).** Binary reward = all
  postcondition sensors pass; clip-higher; no KL loss; no reward-std normalization
  and length normalization (Dr.GRPO); leave-one-out advantages (RLOO); DAPO group
  filtering of all-pass/all-fail groups; no entropy bonus but monitor token entropy
  (0.3–1.0 band). Framework: veRL or rLLM. Compute: rented 8–32 H100s;
  AceCoder's precedent (~80 steps / ~6 h for meaningful gains) sets the entry cost.
- **Reward shape.** Per-action postcondition reward (dense, non-gameable) plus a
  terminal goal-reached bonus shaped by potential Φ(s) = StateValue(s) —
  potential-based shaping is policy-invariant, so the planner's own value function
  safely densifies the skill's signal.
- **Caveat adopted from arXiv:2508.03501:** compact-filtering must *not* mask
  looping-failure trajectories — those are the negative examples that teach
  loop-breaking. LEDGER's per-action attempt budgets (§12.3) already surface loops
  explicitly; keep them in the training set with negative reward.

### 14.3 Surface B — the planner: verifier-then-value (the AlphaZero path)

Attacks the paper's §6 BFS scalability wall, in the order the field validated:

- **Step 1 — inference-side first, no training.** Wire a **hybrid verifier** as node
  scorer: execution-based (sensors on candidate states) + execution-free (LLM judge
  over the subgraph trajectory), per R2E-Gym (hybrid verifiers → 51% Verified) and
  SWE-Search (+23% relative, scaling with search depth; its git-commit-tree +
  backtracking design is the closest published analog to 𝒢). *Gate: must beat the
  BFS `StateValue` baseline on the §11 suite before any training is justified.*
- **Step 2 — train V(s) and π(a|s), swap UCB1 for PUCT.** Train on journal
  Monte-Carlo returns (goal-reached terminals) and observed action choices — the
  SWE-Gym "train a verifier on your own trajectories, reuse it for search" pattern.
  Proposition-set states are tiny: a small set-encoder/MLP or GNN over 𝒢, trained
  locally on the 5090s in minutes — the planner nets need none of the H100-scale
  budget that Surface A does. Synthetic curriculum from
  `src/environments/task_generator.py` (randomized branching/trap-density/horizon)
  pretrains before real-journal fine-tuning.
- **Determinism preserved:** networks emit only priors and values; the action is
  still argmax over a journaled PUCT table; the weight hash is stamped into every
  `PLAN` entry; `ledger replay` pins weights by hash. Anchor the value net against
  exact BFS on small subgraphs as a drift alarm.

### 14.4 Surface C — L2 as a learned dynamics model

Upgrade count-based L2 (Eq. 2/6) to an ensemble transition model P(s′|s,a) over
proposition embeddings, with ensemble disagreement as the confidence p₂ feeding the
λ-penalty (Eq. 17). This is Dyna with 𝒢 as the model store, and it is the concrete
mechanism for open question §12.6 (cross-repo transfer): a learned model carries
`pytest`-shaped / `cargo`-shaped priors into repos where count tables start empty.

### 14.5 Failure-mode mapping

| Field's converged failure | LEDGER's answer |
|---|---|
| Reward hacking (weak-test exploitation) | Binary all-sensors reward; frozen held-out acceptance sensors (§7.1); lie-detection incidents as negative reward (§9.4) |
| Entropy collapse | clip-higher; entropy monitored in training telemetry; DAPO group filtering |
| Length inflation | Dr.GRPO length normalization |
| Long-trajectory credit assignment | Structural: planner owns the horizon; skills get per-action rewards (§14.1.3) |
| Context blowup | Structural: skills never see whole-episode context |
| Rollout infra tax | Sunk cost: sandbox worktrees + sensors + journal already built (M2) |

### 14.6 Gated weight promotion

New weights (skill, value, prior, or dynamics) enter production only through the §11
harness — the kraken-quant validation-gate pattern:

- plan variance across seeds must remain **0**,
- per-layer divergence rate must not regress,
- incident (false-claim) rate must not increase,
- SWE-mapped stress-suite success must be ≥ incumbent.

Weight lineage (base model, dataset journal-range hashes, recipe config, eval report)
is itself a journal entry — the audit chain covers the *training* provenance, not
just the runtime decisions.

### 14.7 RL milestones (extend §13)

| # | Deliverable | Exit criterion |
|---|---|---|
| M6 | Hybrid verifier as search node scorer (no training) | beats BFS `StateValue` on §11 suite (SWE-Search-style margin) |
| M7 | RFT skill bootstrap (7B–14B LoRA, local) | postcondition pass-rate up vs base; plateau documented |
| M8 | Planner V/π nets + PUCT (local training) | catalog cap lifted past |A|=20 with no success regression; replay still bit-exact |
| M9 | GRPO++ skill run (rented 8–32 H100, veRL/rLLM) | SWE-bench Verified (fixed harness, version-pinned) ≥ RFT checkpoint; promotion gates green |

Sequencing note: M6 and M7 are independent and cheap — run both before committing to
either training track; each produces its own go/no-go evidence.

---

*Name is a working title. The framework being leveraged — and the thing this doc
should be judged against — is GATS as specified in arXiv:2607.08894v1 and
implemented in `gats\gats2\src`.*
