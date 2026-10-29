# Technical TL;DR per slide

Deck: talk/deck/index.html (19 main slides). Concepts first, then the one message the slide carries.

## 1. What did your agent actually do?
Concepts: the terminal is a real LEDGER run. A local 27B model fixed a failing test in 4 steps with 0 LLM planning calls (the planner, not the model, picked each step). `verify-chain` checks a hash chain (each log entry stores the hash of the one before, so any edit breaks every later link).
Message: the next jump in agent reliability is in the system around the model, not the model.

## 2. About me
Concepts: production RAG (retrieval-augmented generation: fetch documents, put them in the prompt) and agents at Disney and Google, over high-traffic, fast-changing data.
Message: at scale, rare failures are daily; the first question about any agent output is what it actually did; the model was rarely the bottleneck.

## 3. Who remembers AI before...
Concepts: three fixes, each from outside the model. Retrieval puts the source in the context (answers you can check). Chain of thought asks for intermediate steps (work you can follow). Reasoning models came from RL (reinforcement learning) against a checker: right answer, tests pass (work you can hand off). Last question: who used LLMs before ChatGPT (BERT, GPT-2, T5, GPT-3), all built on the transformer from Google's 2017 paper Attention Is All You Need (Vaswani et al.).
Message: each jump in usability and trust started outside the model.

## 4. The dark ages (pre-ChatGPT)
An animated caveman rubbing sticks ("Running GPT-2, circa 2019") sets a light tone.
Concepts: real outputs from a notebook (Hugging Face transformers, run 10-29). BERT (2018) fills a blank: "The capital of France is [MASK]." gives paris, 42%. A BERT tuned for questions (SQuAD) can only point at words you paste in; asked "What did my agent actually do?" it answers "Microsoft". GPT-2 (2019) answers a question, then writes the next question itself. Alongside a tuned model, ChatGPT added a harness: turns, a stop at the end of its turn, the conversation re-sent every turn, a hidden system prompt.
Message: the chat window is a harness.

## 5. Harnesses grow, then models absorb them
Concepts: a harness is everything around the model, in five lanes: context, reasoning, tools, permission, verification. Chain of thought took PaLM 540B on GSM8K (grade-school math) from 18% to 57%, but only at ~100B parameters. Neither scale nor the prompt worked alone. o1 and R1 then used RL with rule-based rewards to train that procedure into the weights. Frontier coding agents (Claude Code, Codex, Antigravity) now run a simple think, tool, observe loop.
Message: the harness finds what works, training absorbs it, scale makes it absorbable. Contact with the world (what's true, what's allowed, what happened) can't be absorbed, and verification is the thinnest lane.

## 6. Today's harness is tomorrow's model
Concepts: harness traces become training data and harness checks become rewards ("wrappers become weights"). Already absorbed: step-by-step to reasoning models, voting to self-checking, ReAct text to native tool calls, tree search to learned backtracking (R1-Zero learned "wait" and rechecking from rewards alone). Next to go: compaction, subagents, skills, plan search. Goodhart's law: a measure that becomes a target stops measuring. ImpossibleBench: stronger models game tests more. METR caught o3 reward hacking (exploiting the scorer) in 30% of RE-Bench runs.
Message: procedures get absorbed; the check can't be, because a verifier the model trains against becomes a target. Build sensors, gates and the record outside the model.

## 7. Task length doubles about every 4 months
Concepts: METR time horizon. Time skilled humans on tasks, fit success vs human task length per model, read off the 50% crossing. TH1.1: GPT-2 3 s, GPT-4 4 min, Claude 3.7 1 h, GPT-5 3.4 h, Opus 4.6 12 h. Doubling 129 days since 2023 (95% CI 104 to 158). Trend: work week Dec 2026, work month Sep 2027. Caveats: past 16 h the suite is thin, and horizon is task length, not how long an agent runs unattended. The 80% toggle shows horizons 5 to 10x shorter. Responsibilities: no completion state, judged on invariants held (conditions kept true), one stateful agent, not a cron job or pipeline.
Message: after month-long tasks comes a different kind of request, responsibilities, and nothing on this curve measures those yet.

## 8. Seven gaps that limit long-horizon agents
Concepts:
- Reliability: per-step accuracy compounds (0.99^100 = 37%); pass^k (succeed on all k tries) falls fast (tau-bench GPT-4o retail: 61% once, under 25% for 8 of 8).
- Trust: ImpossibleBench (tests contradict the spec): GPT-5 "passed" 76% by gaming tests.
- Auditability: EU AI Act Art. 12 requires lifetime event logs for high-risk systems from Dec 2027.
- Reproducibility: temperature 0 still gave 80 distinct answers in 1,000, because batch size changes floating-point reduction order.
- Planning: PlanBench, o1-preview 98% on Blocksworld, ~24% at 20 to 40 steps.
- World models: an agent deleted a production database, then misreported rollback as impossible.
- Groundedness: AlphaGeometry, a neuro-symbolic system (neural proposals, symbolic checks), solved 25 of 30 olympiad problems.
Message: a better model shrinks these gaps; it doesn't remove them. They're system properties.

## 9. If the gaps closed
Concepts: compounding with a check. 1% per-step error over 100 steps gives 37% success. A check catching 9 in 10 errors leaves 0.1% per step, giving 90%. Same model. The horizon multiplier is 1/(1 - catch rate): 10x at 90%, 100x at 99%. It assumes the check fails independently of the model, which self-grading violates.
Message: verification, not intelligence, moves the 37% cliff from step 100 to step 1,000; independent checks unlock 99% horizons, behavioral regression tests and auditable agents.

## 10. The LLM is the last resort (GATS)
Concepts: GATS (arXiv:2607.08894). UCB1 tree search (pick the option with the best average result plus an exploration bonus for rarely tried ones) over a persistent state-transition graph that survives between runs. Each predicted transition goes to the cheapest layer that can answer it: L1 symbolic contracts (exact, free), L2 statistics from its own runs, L3 cached LLM call for novel transitions only. 0 LLM calls during planning (50 one-time bootstrap calls); repeat runs make none. Stress test (12 categories, 120 tasks): GATS 100%, LATS 88.9%, ReAct 23.9%, 0% variance across seeds. On live Llama 3.2: GATS 100%, LATS under 1%, ReAct 36.3%.
Message: an agent with a map it checks and corrects (the sailor with a chart) beats one estimating from intuition, and its plan quality doesn't depend on the model.

## 11. Paths merge, dead ends close, the goal holds
Concepts: an animated GATS search over its state-transition graph. Start: tests failing. Explore: UCB1 picks branches (average result plus an exploration bonus). Merge: two paths that reach the same state become one node and share statistics (a plain tree would keep two copies). Prune: dead ends (test edit refused, merge conflict, no path) are marked and never searched again. Goal: the best path reaches tests pass, PR open; the graph is kept, so a repeat run makes no LLM calls.
Message: the map is kept, so the agent doesn't hit the same rock twice.

## 12. Keep the search space small
Concepts: tree search cost grows as branching^depth (b^d): 20 actions, depth 10 is ~10^13 sequences. LEDGER searches 5 coarse plan actions (RunSuite, AuthorPatch, ApplyPatch, Commit, CreatePR): 5^4 = 625. Two altitudes: search plans coarse steps; the LLM fills one open-ended step (the diff) whose result the test suite measures.
Message: keep the searched space small and push open-ended work inside a single checked step.

## 13. Give the model zero trust (LEDGER)
Concepts: LEDGER as an experiment: the model only writes patches.
- It can't set state: sensors report tests pass, repo clean, recoverable.
- It can't claim success: a claim that contradicts sensors becomes an INCIDENT and the planner routes around it.
- It can't pick or run actions: GATS picks, risk classes gate execution, and it escalates with a named reason (`infeasible`).
- The journal is hash-chained. `ledger why` prints the recorded UCB table behind a choice, `replay` reproduces the run bit-exact (deterministic ties, no RNG), `verify-chain` catches a single edited field.
- Same guarantees on Claude, OpenAI or a local model.
Message: to know what your agent did, ask the record, not the model. The cost is flexibility; the question is how much reliability it buys.

## 14. What zero trust bought
Concepts: published numbers for typical agents vs LEDGER's own runs (explicitly not head-to-head).
- Impossible tasks: game tests vs say `infeasible`.
- Weak model: ReAct 36%, LATS under 1%, GATS 100%.
- Repeatability: 80 answers in 1,000 vs bit-exact replay.
- The record: a transcript vs a hash chain.
- The crc case: the test asserted CRC-32 is linear over xor, but its all-ones init and final xor make it affine, so no correct CRC passes. The Claude author rewrote the test 4 of 4 times and the journal certified success (reward hacking of the harness itself).
- The fix: a protected-test-edit sensor plus a sanctioned INFEASIBLE reply. After it, the local 27B named the contradiction and left the tests alone (10/10 rubric).
Message: the fix for a lying success signal is a sensor, not a better model; "can't be done as specified" is the key skill for risky work.

## 15. The model is a part you swap
Concepts: guarantees live in the harness (state, checks, record), so swapping Claude for a local 27B didn't move them. The harness doubles as an RL environment: sensors are rewards, worktrees are rollouts, the journal holds trajectories, and checked per-step outcomes give credit assignment. Gated self-improvement: `ledger improve` writes failing tests for its own bugs; the gate caught 2 bad changes in 5. Monday habits:
- a runnable check per task;
- context as a budget (it degrades as it fills);
- review from a fresh context;
- repeated corrections become rules, skills or hooks.
Message: invest in the harness, because models change every few months and the harness keeps its value; the checker must stay outside what it checks.

## 16. Confidence is not evidence
Concepts: four predictions.
- Evals report consistency (pass^k) and replayability, not just success rate.
- Regulated work requires deterministic replay of agent actions.
- The LLM moves down the stack, and verification is where products differ.
- "I can't do this as specified" becomes a scored capability.
Message: "tests pass" from an agent is a claim; the test output is evidence.

## 17. We can only prompt what we can foresee
Concepts: a prompt encodes steps and a finish line, so it covers only the scenarios its author simulated. Intent encodes purpose, conditions to keep true, limits, budget and escalation rules. The control loop runs forever: sense (what's true), compare (vs target state), plan (simulate, pick), act (irreversible steps gated), everything journaled, exceptions only back to the human. METR's 2025 RCT (randomized controlled trial): 16 experienced developers were 19% slower with AI tools while believing they were 20% faster, so round trips are a hidden cost.
Message: prompts scale with our foresight; plans scale with the agent's. Write intent the agent can check itself against, and keep the record that lets you check it.

## 18. Quick show of hands
Concepts: fun audience questions, one per press: has your agent forgotten the plan halfway through? Context rot (answers get worse as the context window fills) or context bloat (old output piles up)? Usually both. What's a handoff file? A short note after every task (what we're doing, what changed, what failed, what's next) so a fresh session can pick up.
Message: write it down, clear the context, start fresh.

## 19. Thank you
Concepts: LEDGER installs (MIT, free) from maureesewilliams.io/ledger; QR codes to the paper, the repo and the blog.
Message: try it, break it, and tell me where it fails.
