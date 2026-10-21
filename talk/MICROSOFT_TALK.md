# LLMday NYC talk: What Did Your Agent Actually Do?

Oct 29, 2026, Microsoft Experience Center. 30 min, Intermediate (accepted CFP).
Deck: talk/deck/index.html (N notes, F fullscreen, arrows); `python talk/deck/build.py`
writes slide PNGs, a contact sheet and deck.pdf to talk/deck/out/.
Demo repo: `python talk/demo/make_demo_repo.py <dir>`. Rehearsed 2026-10-21 on
qwen3.8:27b (GPU 1): goal reached in 4 steps, 1 min 58 s, one-line fix;
verify-chain, why, replay OK; one edited field gives "chain BROKEN: entry 17".
Needs `--test "python3 -m pytest -q"` here (default python has no pytest).
25-min talk, 10 slides; see the timing map.

One idea the room should leave with: reliability over long horizons doesn't come
from a smarter model, it comes from a harness that never trusts the model's
claims about the world and can prove what happened afterwards.

---

## Timing map (25 min talk, 10 slides, about 2.5 min each; Q&A after)

The deck shows each main slide's target start time and a running clock
(bottom right; T resets it). Everything after the close is backup.

| # | Slide | Starts at |
| --- | --- | --- |
| 1 | Title + cold open (pre-started run or recording) | 0:00 |
| 2 | Long, risky work breaks agents (curve, confidence is not evidence, risk-averse questions) | 3:00 |
| 3 | GATS: layers + stress test | 5:30 |
| 4 | Addendum: a claim nothing checked (preprint vs repo vs live Llama) | 8:00 |
| 5 | LEDGER: plan, skill, sensors, journal; incidents; gates | 10:00 |
| 6 | The answer to the title: why, replay, tamper | 13:00 |
| 7 | Evidence: graded problem solving + the reward hack | 15:00 |
| 8 | Where harnesses go: product, RL environment, gated self-improvement | 17:30 |
| 9 | Habits for any coding agent: two laws, four tips | 20:00 |
| 10 | Close + predictions | 23:00, done by 25:00 |

Backup, in order: addendum A1-A3, then the detail slides behind each main
slide (curve, risk, GATS, scaling limit, inversion, state, altitudes, safety,
trust, hardbench, local results, reward hack, bench lessons, RL, self-improvement,
predictions, two laws, tips 1-4 with full prompts, verification ladder).

--- | --- | --- |
| 1 | Cold open (pre-started run or recording) | 0:00 |
| 2 | Title | 2:00 |
| 3 | Reliability compounds | 2:20 |
| 4 | Confidence is not evidence | 3:20 |
| 5 | GATS in one slide | 4:20 |
| 6 | Stress test (mention the addendum) | 5:35 |
| 7 | Neural proposes, symbolic disposes | 6:50 |
| 8 | State is what sensors say | 8:05 |
| 9 | Safety is graph semantics | 9:05 |
| 10 | Audit you can run (why, replay, tamper) | 10:05 |
| 11 | Grade problem solving | 11:20 |
| 12 | Local model results | 12:20 |
| 13 | The harness got reward-hacked | 13:20 |
| 14 | The harness is the product | 14:20 |
| 15 | Five predictions | 15:05 |
| 16 | Two laws | 16:05 |
| 17-20 | Tips 1-4 | 17:05, 18:05, 18:50, 19:35 |
| 21 | Close | 20:20, done by 21:00 |

Backup, in order: addendum A1-A3, risk-averse questions, scaling limit, two
altitudes, trust boundaries, audit the evaluator, RL training rig,
impossibility and self-improvement, verification ladder.

If running long, cut: 8 (state), 14 (harness is product), tip 3.

--- | --- | --- |
| 0 Cold open (live) | 0:00-2:30 | 1-2 |
| 1 The problem: long horizon x risk | 2:30-6:00 | 3-5 |
| 2 GATS, the paper | 6:00-11:00 | 6-9 |
| 3 LEDGER architecture | 11:00-18:00 | 10-15 |
| 4 Evidence and what broke | 18:00-23:30 | 16-19 |
| 5 The future of harnesses | 23:30-28:30 | 20-24 |
| 6 Close | 28:30-30:00 | 25 |

---

## 0. Cold open

### Slide 1: [terminal, no title]
- Live: `ledger run "fix the failing test" --backend ollama --model qwen3.8:27b`
  on a small buggy repo. Plan prints, gate asks, patch lands, suite goes green.
  Then `ledger verify-chain` and `ledger why 2`.
- Say: "That was a local model on one GPU, no cloud. Every step it took is in a
  hash-chained log, and I can ask it why it chose step two over every other
  option and get the actual numbers it used, not a story written afterwards."
- Fallback: record the run the night before.

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
- Say: "Neural proposes, symbolic disposes."

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
  failed. The harness checked the bar, not whether the bar had moved.
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
4. Harnesses ship verifiers as the moat; models are interchangeable parts.
5. "I can't do this as specified" becomes a scored capability.

## 6. Close

### Slide 25: Close
- "Confidence is not evidence. Build the harness that asks for evidence."
- QR to the paper and kyberos.ai/ledger (see Landmine 5).

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

## Landmines

1. **Paper Table 1 (100 / 92 / 64).** Addressed by the deck's Addendum
   (slides A1-A3) and the post maureesewilliams.io/writing/gats-preprint-addendum
   (published 2026-10-21). Never cite 92/64 or b=5 84%; Table 2's budget
   ablation includes that 84%, so don't cite it either.
2. "Zero LLM calls" always needs "during planning".
3. Rubric scores are operator judgment by the same model family as the author;
   pass counts and test-edit checks are mechanical. Say so if asked.
4. hardbench reruns after harness fixes aren't first-contact results.
5. kyberos.ai publishes no install command (LEDGER sold as enterprise access).
   Showing the one-liner on stage is a positioning change; decide first.
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
- Determinism with a stochastic LLM? Plans are deterministic; skill outputs are
  journaled as blobs, so replay is bit-exact.

## Addendum (deck slides 28-31)
A1 table: preprint vs repo pipeline vs live Llama 3.2. A2: provenance (simulated
backend + 25 API-Bank tasks; calls 13.3/17.5/36.9; ReAct optimality 0.54; LATS
b=10 gap unexplained) and the other v1 errors. A3: what holds, and locked
PlanBench (48 vs 63 of 150 failed the broad claim; v2.1 78/90 vs 32/90).

## To decide
- Show the install one-liner? (Landmine 5)
- Addendum is main slide 4; A2-A3 detail stays in backup.
