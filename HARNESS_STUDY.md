# HARNESS_STUDY: what open-source coding agents do that LEDGER doesn't

Studied 2026-10-08 from source: shallow clones, read only, nothing run.
CODEX_PARITY.md was built from docs; this one cites code. Paths are relative
to each repo at the commit listed.

| repo | commit | date |
|---|---|---|
| openai/codex (codex-rs) | e974aad | 2026-10-08 |
| google-gemini/gemini-cli | 44d764e | 2026-10-07 |
| sst/opencode | 663fbd7 | 2026-10-07 |
| cline/cline | 49276bc | 2026-10-07 |
| block/goose | 9560429 | 2026-10-07 |
| Aider-AI/aider | 5dc9490 | 2026-05-22 |
| OpenHands/software-agent-sdk | 69e2688 | 2026-10-07 |
| SWE-agent/SWE-agent | 3ea751c | 2026-07-16 |
| SWE-agent/mini-swe-agent | 04d809c | 2026-09-03 |
| aorwall/moatless-tools | 011ead5 | 2025-09-01 |
| aorwall/moatless-tree-search (SWE-Search) | bc66ea6 | 2025-01-13 |
| OpenAutoCoder/Agentless | 5ce5888 | 2024-12-22 |

## The three findings that matter

**1. Every harness detects loops by hashing actions. None hashes outcomes.**
gemini: sha256(tool+args), a cycle of length 1-5 repeated 5x
(`services/loopDetectionService.ts:313-349`). cline: 3 identical calls soft,
5 hard (`sdk/packages/core/src/runtime/safety/loop-detection.ts:66-116`).
opencode: 3 identical calls raise a `doom_loop` permission ask
(`src/session/processor.ts:356-379`). OpenHands: action AND observation equal
4x (`conversation/stuck_detector.py:325-370`, thresholds
`conversation/types.py:150-161`). All of them would have let relay r2 through:
nine different patches, one identical ImportError. LEDGER is the one
architecture where outcome detection is natural, because sensors already
produce the state: make the failure signature a proposition and UCB1's visit
counts do the detecting.

**2. No harness leaves verification to the policy.** moatless fuses edit and
test (`actions/apply_change_and_test.py:100-126`) and runs related tests after
every StringReplace (`actions/string_replace.py:369-374`). aider lints, and
optionally tests, after every edit (`coders/base_coder.py:1599-1625`).
opencode appends LSP diagnostics to every edit result (`src/tool/edit.ts:197-201`).
SWE-agent's submit refuses once if code changed after the last reproduction
run (`tools/review_on_submit_m/bin/submit:33-40`). LEDGER leaves RunSuite to
UCB1, which is how datum went 12 AuthorPatch -> ApplyPatch pairs blind. Fix it
with a contract, not exploration tuning.

**3. Only an exact grep finds a renamed import.** moatless FindCodeSnippet is
`grep -n -r` and hits the stale import line (`actions/find_code_snippet.py:93-128`).
aider's repo map misses it: tags capture defs and calls, not imports
(`queries/*/python-tags.scm`), and the graph keeps only names both defined and
referenced (`repomap.py:468`), so a dangling name has no edge. But refs minus
defs is exactly the dangling-symbol set. Agentless skeletons drop imports
entirely (`agentless/util/compress_file.py:15-29`).

What LEDGER already has that none of them do: a hash-chained journal (none
chain; SWE-agent replay re-executes, it does not reproduce,
`run/run_replay.py:138-171`), zero LLM calls at plan time, action contracts
with lie detection, signed receipts.

## Build order

1. **Failure signature as state (G2).** `sensors.py`: normalize a RunSuite
   failure to (phase collect|test, exception type, missing name or module,
   file), strip line:col and addresses (Agentless does this for flake8,
   `agentless/util/postprocess_data.py:75-101`), hash to `fail.sig:<12hex>`.
   Same failure, same node. `actions.py`: AuthorPatch requires repeat < 3; at
   3 only escalation moves stay applicable (symbol lookup, wider context,
   whole-file rewrite, other model, ask); at 5, ESCALATION `stuck` naming the
   signature. Reset only when the signature changes or a sensor improves
   (cline resets on a productive turn, `session-runtime-orchestrator.ts:1362-1383`).
   Feed the tried patches back into the author prompt with "propose a
   different approach" (moatless `search_tree.py:365-370`,
   `feedback/reward_feedback.py`). Response shape everywhere is nudge, then
   halt (OpenHands `stuck_detector.py:218-248`; gemini `core/client.ts:1300-1327`).
   Fixture: relay `readstate-rtm` r2.

2. **`suite.stale` proposition (G3).** ApplyPatch adds it, only RunSuite
   removes it, AuthorPatch and goal satisfaction require it absent. Goal props
   count only when observed after the last mutation, never when predicted.
   Fixture: datum `sketch-solver`.

3. **Dangling-import sensor and symbol grep (G4).** stdlib `ast`: for each
   repo-local `from x import n`, check x defines n; a miss becomes
   `imports.broken` with both paths as evidence. On ImportError/NameError text,
   regex the name, then `git grep -nw <name>` and `git log -S <name> -n 3`
   into `select_context`. One step on relay. Extends the uncommitted
   `failure_source_context` (`skills_llm.py:199`).

4. **Pre/post apply gate with revert (G5).** `ast.parse` plus sensor 3 on
   touched files before and after ApplyPatch; new errors revert the worktree
   and go back to the author. SWE-agent `tools/windowed_edit_linting/bin/edit:95-119`;
   Agentless drops syntax-error patches, `repair/repair.py:590-597`.

5. **Hunk matcher ladder (G5).** Locate hunks by content and ignore header
   numbers: exact, rstrip, strip, unicode-normalized (codex
   `apply-patch/src/seek_sequence.rs:29-108`), relative indent (aider
   `coders/search_replace.py:528-578`), then block anchor on first/last line
   with >= 0.65 similarity and an oversized-match guard (opencode
   `src/tool/edit.ts:288-357,731-737`). Journal the tier that fired. On a miss
   return the nearest block plus a numbered preview (goose
   `developer/edit.rs:157-196`; aider "did you mean",
   `coders/editblock_coder.py:84-124`). aider measured 9x more edit errors with
   flexible patching off (`website/docs/unified-diffs.md:306`). Supersedes
   `repair_hunk_counts`.

6. **Windows sandbox (G1).** Phase 1, ctypes Job Object: KILL_ON_JOB_CLOSE
   plus memory/process limits, create suspended, assign, resume (gemini
   `sandbox/windows/GeminiSandbox.cs:247-307,371-387`; codex
   `utils/pty/src/win/job.rs:44-54`). This also kills orphaned children for
   free (the v0.8.2 bug) and contains MCP servers. Phase 2: restricted token
   plus Low integrity, label the worktree Low, leave `.git` and `.ledger`
   unlabeled (`GeminiSandbox.cs:422-489`; codex keeps `.git` read-only,
   `protocol/src/permissions.rs:40-42`). Fail closed if setup fails (codex
   `sandboxing/src/windows.rs:98-103`). Parse "Access is denied" / 0x80070005
   into a `sandbox.denied(path)` observation for the gate (gemini
   `windowsSandboxDenialUtils.ts:18-85`; codex asks to retry unsandboxed,
   `core/src/tools/orchestrator.rs:429-499`).

7. **MCP lifecycle (G6).** Close servers at episode end (`executor.py:1168`
   never does). Lazy connect under a per-server lock (cline
   `extensions/mcp/manager.ts:181-238`). 30s start, 300s call timeouts (codex
   `codex-mcp/src/rmcp_client.rs:106-110`), `notifications/cancelled` on
   timeout (goose `agents/mcp_client.rs:620-640`). Servers in the episode's Job
   Object (codex `rmcp-client/src/stdio_server_launcher.rs:510-518`). Scrubbed
   env with `${VAR}` expansion for templated args (gemini
   `tools/mcp-client.ts:2342-2382`).

8. **Per-action snapshots (G8).** Separate git-dir, `add -A` + `write-tree`
   before each mutating action; tree hash into EXEC and the receipt; undo is
   read-tree + checkout-index after backing up the current tree (opencode
   `src/snapshot/index.ts:318-443`; cline `session/checkpoint-restore.ts:42-107`).
   Receipts then name the exact tree each action produced.

9. **Graded RunSuite reward (G9).** collection error < more failures < fewer
   failures < pass, as deltas against the parent, decaying on no improvement
   (moatless `value_function/coding.py:133-191`). Feeds L2 instead of a binary.
   Best-of-N: vote over AST-normalized diffs (Agentless `repair/rerank.py:156-245`)
   rather than first deterministic winner.

10. **Journal-derived author context (G7).** No summarizer: goal, touched
    files, signatures tried, last N attempts in full, older ones as pointers
    (cline `extensions/context/compaction-shared.ts:425-462`). If chat ever
    needs an LLM condenser, journal the condensation (forgotten ids + summary)
    so replay reuses it (OpenHands `event/condenser.py:11-95`).

Smaller ideas: policy rules carrying match/not_match examples checked at load
(codex `execpolicy/src/parser.rs:349-407`); recursive shell parsing including
`bash -c`, unparseable means ask (gemini `policy/policy-engine.ts:466-581`);
reject a compaction that grew the token count (gemini
`context/chatCompressionService.ts:696-705`).

Deliberately skipped: LLM loop judges (gemini, after 30 turns), LLM edit
fixers, LLM value functions (moatless). Each puts a model in the plan loop.

## Per-agent notes

- **codex:** the strongest sandbox (Windows restricted token + capability SIDs
  + Job Object + WFP network block, `windows-sandbox-rs/src/token.rs:500-523`,
  `wfp.rs:320-333`; landlock/bwrap; seatbelt), Starlark exec policy where the
  strictest decision wins (`execpolicy/src/policy.rs:403`), compaction at 90%
  keeping 20k tokens of recent user turns (`core/src/compact.rs:686-727`). It
  removed its ghost-commit undo.
- **gemini-cli:** C# Windows helper (Low integrity, Job Object), sandbox
  denial becomes an expansion request (`tools/shell.ts:1054-1105`), priority
  policy engine, 4-tier edit plus an LLM fixer, compaction at 50% to an XML
  snapshot with a self-critique pass, shadow-git checkpoints
  (`services/gitService.ts:184-234`).
- **opencode:** no OS sandbox; 9 cascading edit replacers; LSP diagnostics
  after edits; prunes old tool output past 40k tokens
  (`src/session/compaction.ts:273-317`); write-tree snapshots per step.
- **cline:** consecutive-mistake counter (limit 6, CLI 3), soft/hard loop
  thresholds, exact-only editor, a deterministic touched-file list that
  survives compaction, stash checkpoints with transactional restore.
- **goose:** nearest-match edit errors; middle-out retry when the summarizer
  itself overflows (`goose-context-management/src/summarize.rs:36-75`). Its
  repetition limit is off by default, and the seatbelt sandbox was removed in
  v1.25.
- **aider:** tree-sitter def/ref graph with PageRank, error-text identifiers
  boosted (`repomap.py:432-443`); whole/diff/udiff/architect formats (unknown
  models default to whole, `models.py:131`); architect/editor 85%; reflection
  capped at 3.
- **OpenHands SDK:** 5-pattern stuck detector, nudge then halt; LLM condenser
  stored as an event; critic refines to >= 0.7, at most 3 rounds
  (`critic/base.py:20-114`); append-only event store with forks.
- **SWE-agent / mini-swe-agent:** format-error requery at most 3 with failed
  attempts kept out of history (`sweagent/agent/agents.py:789-813`);
  flake8-gated edit with revert; old observations collapsed to "(n lines
  omitted)" (`history_processors.py:85-176`). mini is the ~100-line baseline:
  step/cost/time limits and a format-error counter, nothing else.
- **moatless / SWE-Search:** UCT plus about 10 shaping terms
  (`selector/selector.py:222-303`): quadratic penalty on repeated action
  types (`:518-545`), a revisit bonus for a good parent whose code-change child
  scored badly (`:338-370`). Siblings rotate agent settings
  (`expander.py:61-83`), duplicate-args siblings are never executed
  (`agent/agent.py:144-150`), and a ViewCode reward that values novelty
  (`coding.py:193-211`) is the nearest thing to information-gain
  observation.
- **Agentless:** fixed file -> function -> line localization, N sampled
  patches, reproduction tests valid only if they fail on the original code
  (`test/run_reproduction_tests.py:59-63`), regression filtering, majority vote.
