# LLMday NYC talk: What Did Your Agent Actually Do?

Oct 29, 2026, Microsoft Experience Center. 30 min, Intermediate (accepted CFP).
Deck: talk/deck/index.html (N notes, F fullscreen, arrows); `python talk/deck/build.py`
writes slide PNGs, a contact sheet and deck.pdf to talk/deck/out/.
No live demo (no time in 25 min). Slides 1 and 6 show real output from the
2026-10-21 run (talk/demo/make_demo_repo.py, qwen3.8:27b on GPU 1: 4 steps,
1 min 58 s, one-line fix; verify-chain, why, replay OK; one edited field gives
"chain BROKEN: entry 17").
25-min talk, 10 slides; see the timing map.

One idea the room should leave with: reliability over long horizons doesn't come
from a smarter model, it comes from a harness that never trusts the model's
claims about the world and can prove what happened afterwards.

Frame (reese, 10-26): lessons learned from large-scale AI and agent platforms. Disney
AI team, about 2 years: RAG and agents for guests planning vacations and asking about
resorts and parks (high traffic, always available, data updated continually). Now at
Google: agents that read tables and historical data and summarize tens of millions of
records for business decisions. Three lessons thread the talk: (1) at scale rare failures are daily, and a
confident answer from stale or partial data is still wrong; (2) when an agent's output
drives a decision, you need what it actually did, not a transcript; (3) the model was
rarely the bottleneck, the system around it was. Employer names stay off the slides.

---

## Timing map (25 min talk, 14 slides + links slide, ~2 min buffer; Q&A after)

Order since 10-26: the past and its patterns, then what's missing, then what
closing it unlocks, then GATS, then LEDGER as the evidence.
The deck shows each main slide's target start time and a running clock
(bottom right; T resets it). Everything after the close is backup.

| # | Slide | Starts at |
| --- | --- | --- |
| 1 | Title + claim, real run output on screen | 0:00 |
| 2 | About me + three lessons + agenda | 0:35 |
| 3 | Who remembers AI before... (show of hands: reasoning, CoT, retrieval; fixes from outside the model unlocked usability and trust) | 1:35 |
| 4 | Harnesses grow, then models absorb them (swimlane timeline 2020-now; harness + scale + RL coupling) | 2:55 |
| 5 | Today's harness is tomorrow's model (absorbed, next to go, the check stays) | 4:25 |
| 6 | Task length doubles about every 4 months (METR horizons, projection, responsibilities) | 6:05 |
| 7 | Small deviations compound (David, Prometheus: drift with no outside check; 99% per step -> 37% after 100) | 8:05 |
| 8 | Seven gaps that limit long-horizon agents (7 gaps, one cited number each) | 8:45 |
| 9 | If the gaps closed + the GATS piece | 10:45 |
| 10 | GATS: layers + stress test (sailor framing in notes) | 12:10 |
| 11 | The limit: keep the search space small | 14:05 |
| 12 | LEDGER, a zero-trust experiment: pipeline, sensors/incidents/gates, and the record (why, replay, tamper) | 14:55 |
| 13 | What zero trust bought: LEDGER vs most coding agents + crc before/after | 16:50 |
| 14 | Where harnesses go + the four Monday-morning habits | 18:45 |
| 15 | Confidence is not evidence + four predictions | 20:55 |
| 16 | CLOSE: We can only prompt what we can foresee (control loop; prompt vs intent) | 21:55, done by 24:05 |
| 17 | Thank you + QR codes + install lines (up through Q&A) | 24:05 |

The old problem slide (0.99^n, confidence is not evidence) is now the first
backup slide; its numbers live on slides 6 and 7.

After the close: the addendum (divider + A1-A3), shown if asked or time allows. Then backup: the detail slides behind each main
slide (curve, risk, GATS, scaling limit, inversion, state, altitudes, safety,
trust, hardbench, local results, reward hack, bench lessons, RL, self-improvement,
predictions, two laws, tips 1-4 with full prompts, verification ladder).

---

## 0. Open

### Slide 1: title, with a real run's terminal output (no live demo)
- Say: "This is a run from last week: a local model on one GPU, no cloud. Every step it took is in a
  hash-chained log, and I can ask it why it chose step two over every other
  option and get the actual numbers it used, not a story written afterwards."

### Slide 2: Title
- Say: "I'm going to argue that the next jump in agent reliability isn't in the
  model. It's in the harness around it."

## 1. The problem

### Slide 3: Reliability compounds against you
- Visual: 0.99^n curve. 99% per step is 37% at 100 steps, 0.004% at 1,000.
- Say: "Every agent demo is short. Real work is long. Per-step accuracy
  multiplies, so the horizon eats you even when each step looks great."

### Slide 4: Risk-averse work is a different game
- Visual: two axes, horizon vs. blast radius. Chatbots bottom-left; deploys,
  payments, infra, regulated code top-right.
- Say: "In risk-averse work the question isn't 'did it usually work', it's
  'can you show me exactly what it did, and can you stop it before the bad step'."

### Slide 5: Where agents fail (the lie)
- Five ways a model asserts world state that isn't true: "tests pass", "file
  written", "precondition met", "safe to deploy", "done".
- Say: "Confidence is not evidence. Most agent failures I see aren't bad
  reasoning, they're the agent believing its own claim about the world."

## 2. GATS (arXiv 2607.08894)

### Slide 6: GATS in one slide
- Graph-Augmented Tree Search: UCB1 tree search over a persistent
  state-transition graph, with a three-layer world model:
  L1 symbolic contracts, L2 learned statistics, L3 LLM prediction (cached).
- Say: "The LLM is the last resort for predicting what an action does, not the
  thing deciding what to do."

### Slide 7: Why layers
- Cheapest trustworthy layer answers first. Paper's open-domain projection:
  L1 60%, L2 30%, L3 10%.
- Say: "Symbolic where you can, learned where you've seen it, neural only for
  the genuinely new."

### Slide 8: What the paper shows
- Use the stress test row (it reproduces exactly): GATS 100%, LATS 88.9%,
  ReAct 23.9% over 12 categories; 0 LLM calls during planning; 0 variance
  across seeds.
- Say "during planning" every time you say zero (50 one-time bootstrap calls).
- See Landmine 1 before using the 100/92/64 main-table row.

### Slide 9: The honest scaling limit
- Paper §6: tested |A|=15, d=20; impractical past |A| about 20, d>10.
- Say: "Search over raw actions doesn't scale. That's exactly why LEDGER
  searches over a small set of plan-level actions and pushes the open-ended
  part down into skills."

## 3. LEDGER architecture

### Slide 10: The inversion
- Visual: two stacks. Typical agent: LLM in the loop choosing every action.
  LEDGER: GATS picks every action; the LLM only lives inside skills (writing a
  patch), and its output is checked by sensors before it counts.
- Say: "LEDGER is an experiment: give the model zero trust and measure what
  that buys. It can't pick actions, set state, or report success."

### Slide 11: State is what sensors say, not what the model says
- Repo as a proposition set: `tests.pass:unit`, `repo.clean`, `patch.applied`,
  `recoverable`. Deterministic sensors produce them.
- Lie detection: claimed effects minus observed effects = INCIDENT in the journal.

### Slide 12: Actions at two altitudes
- Plan level (searched): RunSuite, AuthorPatch, ApplyPatch, Commit, PushBranch,
  CreatePR. Skill level (LLM inside): writing the diff.
- Verify after execute; a falsified edge resets its statistics and the planner
  routes around it.

### Slide 13: Safety is graph semantics
- Risk classes gate execution, not search. Irreversible = an action that deletes
  `recoverable`. Autonomy profiles: attended / supervised / autonomous.
- A denial is an observation the planner routes around, not a crash.
- Escalation has a named reason: `gate_denied`, `budget_exhausted`, `no_path`,
  `infeasible`.

### Slide 14: The ledger
- Append-only hash-chained journal; full UCB table recorded per step;
  bit-exact deterministic plan replay; `why` trace from the recorded numbers.
- Say: "Audit isn't a log you hope someone reads. It's a replay you can run."

### Slide 15: Trust boundaries that users can verify
- Keys sealed in the OS vault, bound to the provider's host, stripped from every
  subprocess the repo can run, scrubbed from journals and prompts.
- Installer refuses a tampered manifest (sha256).
- Runs fully local: same harness on a 27B model on one GPU.

## 4. Evidence and what broke

### Slide 16: Hard tasks, graded on problem solving
- hardbench: research-grade tasks (RK45, Hartree-Fock, SAT) plus three
  impossible tasks (pigeonhole compression, contradictory CRC tests, a compute
  budget no algorithm meets).
- Rubric: recognition, integrity, delivery, efficiency. Not pass rate.
- Say: "The most important skill for a risk-averse agent is saying 'this can't
  be done as specified' instead of gaming the tests."

### Slide 17: Local model, no cloud
- qwen3.8:27b alone on squeeze: one response, correct pigeonhole argument,
  escalated `infeasible`, tests untouched. Score 8/10.
- Fill in the latest local scores before the talk (run log in FINDINGS.md §6).

### Slide 18: The harness certified a lie (crc, run 2)
- Journal said `success`. The author had edited the suite; the hidden suite
  failed. The harness checked that the tests passed, not whether they'd changed.
- Say: "My own harness got reward-hacked. The fix wasn't a better model, it was
  making test integrity a sensor."
- This slide is the credibility slide. Don't cut it.

### Slide 19: The bench almost lied too
- Contamination audit: reruns after harness fixes score a harness tuned on that
  failure; a hidden oracle that encoded one algorithm choice; a task I called
  impossible that the model solved (primes, 10/10, earned).
- Say: "Calibrate oracles against two independent implementations, and write
  held-out tasks after the harness is frozen, by someone who isn't tuning it."

## 5. The future of harnesses

### Slide 20: Models are commoditizing; the harness is the product
- Same harness, swap Claude for a local 27B model, the guarantees don't move.
  Guarantees live in the harness: state, verification, the last mile.

### Slide 21: Harnesses are the RL environment
- Sensors = the verifier. Worktrees = rollout infra. Journal = trajectory store.
- Per-action postconditions give dense rewards: long-horizon credit assignment
  falls out of the graph.
- Three training surfaces: skill policy (RLVR), planner value nets (PUCT replaces
  UCB1), L2 as a learned dynamics model. Weights only promoted if plan variance
  stays 0.

### Slide 22: Impossibility recognition as a first-class metric
- Benchmarks that only count passes reward test gaming. Grade recognition and
  integrity; ship impossible tasks in every suite.

### Slide 23: Self-improvement, gated
- `ledger improve`: LEDGER files its own bugs as failing tests and fixes them on
  a branch, local model only; a human merges.
- Say: "Recursive improvement is only safe when the verifier is outside the
  thing being improved."

### Slide 24: Five predictions
1. Agent evals report variance and replayability, not just success rate.
2. Regulated industries require deterministic replay of agent actions.
3. The LLM moves down the stack: skill author and last-resort predictor.
4. Models become interchangeable; verification is where products differ.
5. "I can't do this as specified" becomes a scored capability.

## 6. Close

### Slide 25: Close
- "Confidence is not evidence. Build the harness that asks for evidence."
- QR to the paper; install one-liners from maureesewilliams.io/ledger (see Landmine 5).

---

## Numbers you can say

| Claim | Value | Source |
| --- | --- | --- |
| Stress test | GATS 100 / LATS 88.9 / ReAct 23.9 | paper Table 6, reproduces |
| Plan-time LLM calls | 0 (50 one-time bootstrap) | Table 1, Table 4 |
| Variance across seeds | GATS 0% | Table 1 |
| Layer split (projected) | 60 / 30 / 10 | Table 4 |
| Scaling limit | about \|A\| 20, d 10 | §6 |
| LEDGER test suite | 623 passing (v0.15.0) | repo |
| Local squeeze | 8/10, `infeasible`, tests untouched | FINDINGS §6 |
| Lie bench (DEF CON) | 15k injections: naked ships 100%, guarded 0, 0% FP | gats2/demos |
| METR 50% horizon (TH1.1) | GPT-2 3 s, GPT-4 4 min, Claude 3.7 60 min, GPT-5 3.4 h, Opus 4.6 12 h, Mythos 17.4 h (>16 h unreliable) | talk/deck/data/metr_horizon_v1_1.yaml (metr.org/assets/benchmark_results_1_1.yaml) |
| METR doubling since 2023 | 129 days (95% CI 104-158); trend: work week Dec 2026, work month Sep 2027 (CI Jun 2027-Jan 2028) | same file; horizon.py |
| 50% vs 80% horizon | Mythos 17.4 h vs 3.1 h; ratio 5-10x across recent models | same file |
| ImpossibleBench | GPT-5 76% on oneoff impossible-SWEbench; Claude mostly edits tests; stronger models cheat more | arXiv:2510.20270, ICLR 2026 |
| METR reward hacking | o3: RE-Bench 30.4% (39/128), HCAST 0.7% (8/1,087) | metr.org/blog/2025-06-05-recent-reward-hacking |
| EU AI Act Art. 12(1) | "automatic recording of events (logs) over the lifetime of the system"; Annex III duties from 2 Dec 2027 (Reg. (EU) 2026/1744, OJ 2026-07-24) | artificialintelligenceact.eu/article/12 |
| Nondeterminism | 1,000 samples at temp 0, Qwen3-235B: 80 unique | thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference |
| PlanBench, o1-preview | Blocksworld 97.8% (587/600), Mystery 52.8%, 20-40-step plans 23.63% | arxiv.org/html/2409.13373v1 |
| Replit incident | agent deleted prod DB during a code freeze, said rollback impossible; it wasn't | oecd.ai incident 2025-07-19 |
| AlphaGeometry | 25/30 olympiad geometry, neuro-symbolic | Trinh et al., Nature 2024 |
| tau-bench | gpt-4o retail pass^1 61.2%, pass^8 <25% (figure only) | arXiv:2406.12045 |
| Vending-Bench | "all models have runs that derail"; failure vs full context r=0.167 | arXiv:2502.15840 |
| Project Vend | phase 1 "we would not hire Claudius"; phase 2 procedures + CEO agent largely ended losing weeks | anthropic.com/research/project-vend-1, -2 |
| Gartner | >40% of agentic AI projects canceled by end 2027; ~130 of thousands of vendors "real"; "agent washing" | Gartner press release 2025-06-25 |
| MIT NANDA | 95% zero return; "core barrier ... is learning" | The GenAI Divide, Jul 2025 |
| METR dev RCT | 16 experienced devs, 246 tasks: 19% slower with AI tools, believed 20% faster | metr.org, Jul 2025 |
| METR caveat | "Time horizon is not the length of time AIs can work independently" | metr.org/notes/2026-01-22-time-horizon-limitations |

## Landmines

1. **Paper Table 1 (100 / 92 / 64).** Addressed by the deck's Addendum
   (slides A1-A3) and the post maureesewilliams.io/writing/gats-preprint-addendum
   (published 2026-10-21). Never cite 92/64 or b=5 84%; Table 2's budget
   ablation includes that 84%, so don't cite it either.
2. "Zero LLM calls" always needs "during planning".
3. Rubric scores are operator judgment by the same model family as the author;
   pass counts and test-edit checks are mechanical. Say so if asked.
4. hardbench reruns after harness fixes aren't first-contact results.
5. Install one-liners on the close slide point at maureesewilliams.io/ledger
   (reese, 10-26; MIT, free). Verified live 10-26: both installers install
   0.15.1 from the site (Windows + Linux/WSL) and refuse a tampered manifest.
   kyberos.ai/ledger still serves the same wheel (release.ps1 mirrors both).
6. Don't say "nobody is building this"; say "almost nobody".

## Q&A backup

- How is this different from guardrails? Guardrails classify text; this checks
  action effects against sensors. Different layer.
- Doesn't search blow up? Yes on raw actions; that's why plan actions are few
  and coarse.
- What about prompt injection? It doesn't stop the text arriving; it stops the
  text causing an unverified action. Intent attacks (a valid but malicious
  goal) need authorization, a separate layer.
- Why not just a better model? Same harness, local 27B model, same guarantees.
- Does this apply to Claude Code, Codex and Antigravity? Backup slide "Do these
  principles hold": the problem and the record hold (stronger models game tests more, per
  ImpossibleBench, arXiv:2510.20270, ICLR 2026); they already have gates; Antigravity's Artifacts (plan, task list,
  walkthrough with screenshots and browser recordings) are the closest to a
  record, but the agent writes them and nothing checks them;
  "model as last resort" is the bet, not a fact. Only cite ImpossibleBench's
  76% with "on one impossible SWE-bench variant" (GPT-5, oneoff).
- Has anyone shipped GATS ideas? Pieces, not the core. Stagehand replays
  cached browser actions with no model call and falls back to the model when
  a step breaks (closest match: L1/L3 with cache). MCP tool annotations mark
  destructive vs read-only tools (risk classes, hints only). Temporal and
  LangGraph replay recorded model outputs (replay, no hash chain, no "why").
  Nobody popular plans without the model, learns transition statistics across
  runs, or journals lies as incidents. The pieces show up where actions
  repeat, which is the scaling-limit slide's point.
- Determinism with a stochastic LLM? Plans are deterministic; skill outputs are
  journaled as blobs, so replay is bit-exact.

## Addendum (deck slides 28-31)
A1 table: preprint vs repo pipeline vs live Llama 3.2. A2: provenance (simulated
backend + 25 API-Bank tasks; calls 13.3/17.5/36.9; ReAct optimality 0.54; LATS
b=10 gap unexplained) and the other v1 errors. A3: what holds, and locked
PlanBench (48 vs 63 of 150 failed the broad claim; v2.1 78/90 vs 32/90).

## To decide
- Install one-liner: shown on the close slide (reese, 10-24).
- Addendum (divider + A1-A3) sits right after the close slide (reese, 10-22); main slide 4 is the limit.
