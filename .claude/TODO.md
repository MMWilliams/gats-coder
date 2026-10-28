# LEDGER — TODO

Goal: make LEDGER usable enough to rival Codex, and ship it as a Kyberos product.

Repo: `C:\Users\reese\gats-coder` · Site: `C:\Users\reese\kyberos`

---

## v0.15.0 (2026-10-19): one-line install, launch like `claude`, sealed keys, new UI

- [x] Installers: `curl -fsSL https://kyberos.ai/ledger/install.sh | sh`,
      `irm https://kyberos.ai/ledger/install.ps1 | iex`; own venv in
      ~/.ledger/venv, PATH set, sha256 enforced. Tested: Windows, WSL Ubuntu,
      tampered manifest refused. NOT tested on macOS (Keychain vault path too).
- [x] Bare `ledger` = the session; `-c`, `--resume [n|id|tag]`, `-p`,
      `ledger "first message"`, `--dangerously-skip-permissions`; sessions in
      ~/.ledger/sessions/<project>/; /status, /sessions.
- [x] Keys: DPAPI/Keychain/Secret Service vault (v1 plaintext migrates),
      host-bound (repo config can't redirect a key), stripped from repo
      subprocesses, exact-value scrub in journals/prompts/errors.
- [ ] macOS: run install.sh + `ledger setup --key` on a Mac (Keychain via
      `security -i` is unit-untested on real hardware).
- [x] Install one-liners live on kyberos.ai/ledger (kyberos e752391, 10-24),
      matching the LLMday close slide; kyberos CLAUDE.md + STRATEGY updated.
- [x] LEDGER license settled 10-26: stays MIT, free to install (kyberos fc0b2fc).
- [x] LEDGER installers hosted on maureesewilliams.io/ledger (blog 37cc7a9,
      Nexus release 106), close slide switched; release.ps1 mirrors the wheel
      + latest.json to the blog. Verified 10-26: Windows + WSL installs, tamper
      refused on both. After the next release.ps1: deploy BOTH sites.
- [ ] Harness laws essay/paper (brainstorm 10-28): absorption, residue, verification
      multiplier (horizon x 1/(1-r) for independent checks), amortization; OS/x87 analogy.
- [ ] Responsibilities research (brainstorm 10-28): goals that hold over time (LTL, linear
      temporal logic: "always X", "X within 30 min"), scheduled sensing, budgets per time
      window, commitment tracking, versioned charters, precedents from escalations.
- [ ] LLMday 10-29: rehearse index.html (15 slides incl. links, targets end 23:00; Antigravity added 10-26) to 25 min (lineage slide 4 added
      10-26, 6e0bfaf); resend backup PDF to Mark if the new slides should be in it.
- [ ] Side effect 10-19: the first install.ps1 test hit a lookup bug and ran
      `winget install Python.Python.3.12` (user scope). Uninstall if unwanted:
      `winget uninstall Python.Python.3.12`.

## Hard math/science bench (design started 2026-10-10)

- [x] `experiments/hardbench/`: tasks rk45, hf, sat built (TASK.md + stub +
      visible tests), hidden suites in `hidden/`, reference solutions in
      `ref/` (all suites pass against them: 15/15, 13/13, 30/30).
      `python run.py <task>` runs LEDGER (claude backend) on a fresh repo under
      `~/ledger-bench/` and grades the worktree on the hidden suite ->
      `results/<task>-<backend>.json`.
- [x] rk45 run 1 found the small-diff cap bug (FINDINGS.md #1, fixed 2e17298);
      run 2: success, 10 min, 1 authoring attempt, 7/7 visible, 8/8 hidden.
- [x] hf: success, 10 min, 1 attempt, 6/6 visible, 7/7 hidden after the
      H4-square oracle was replaced (FINDINGS.md #2). sat: success, 10 min,
      1 attempt, 14/14 visible, 16/16 hidden.
- [x] All 10 tasks built and verified against references: rk45, hf, sat,
      specfun (mpmath oracle), stiff (scipy oracle), hm (own HM checker),
      algo (suffix automaton / BIT / n-queens), factor (sympy oracle; SD6+
      dropped, sympy itself needs >10 min), delaunay (exact incremental
      reference), ising (Wolff reference + exact 4x4 enumeration).
- [ ] specfun: killed 10-11 00:30 after two 1800 s author timeouts
      (FINDINGS #3). Rerun; run.py now passes `--author-timeout 0` (no
      deadline, reese's call 10-11). Then stiff, hm,
      algo, factor, delaunay, ising one at a time with `python run.py <task>`
      (absolute path), write each up in FINDINGS.md, then the one-shot
      `claude -p` baseline per task.
- [ ] RULE (reese 10-11): LEDGER solves problems alone, no Claude backend.
      run.py now needs `--model` (local, e.g. qwen3.8:27b) and refuses
      Claude; every result before 10-11 is Claude-backed, not LEDGER-alone.
      Local runs started 10-11 09:30 with qwen3.8:27b on GPU 1. Ollama
      restarted from `C:\Users\reese\ledger-control\ollama_gpu1.cmd` (GPU 1
      by UUID, Vulkan hidden, 64k context). Order: squeeze (running), then
      `ledger improve --model qwen3.8:27b --max-iterations 5`, then crc,
      primes, rk45, hf, sat, specfun, stiff, hm, algo, factor, delaunay,
      ising, one at a time. Contamination audit: FINDINGS #5.
      squeeze local: proved infeasible in 1 attempt, no code (FINDINGS #6).
- [ ] Local bench PAUSED 10-11 18:15 after rk45: crc 10/10, primes 4/10,
      rk45 4/10. primes and rk45 got 4 EMPTY author replies each (qwen3.8
      thought 14k-21k tokens, LEDGER's OpenAI backend reads only `content`,
      thinking budget only for Bonsai). reese chose "LEDGER fixes it": bug
      filed as failing test 5cb2339 on ledger/self-improve; improve run 3
      failed 2/2 (model rewrites llm.py whole, drops extract_json, guards
      revert). 4 more tries: FIXED by LEDGER alone in 20a19e0 (raises a
      clear "empty content ... reasoning" error). Branch MERGED into main
      10-11 on reese's go (573 passed). Still in main from d9af53b: the wrong
      `Attempt = AgentSpec` alias in cli.py. Root cause (unbounded thinking)
      still open; next: file it as a failing test for `ledger improve`.
      DONE 10-12: bug tests 8b8a122; LEDGER fixed both alone (retry with
      reasoning_effort none; real modes.Attempt import) + state.py tests;
      merged 276fc2e (595 passed). primes rerun (--tag=-run2) REAPED 10-13
      for low memory (UnrealEditor 30 GB + my model 27.7 GB); model
      unloaded. Restart only on reese's go, then rk45 and the other 9. Then hf, sat, specfun,
      stiff, hm, algo, factor, delaunay, ising.
- [ ] `ledger improve` run 2 done 10-11 17:45 (FINDINGS #6): branch
      ledger/self-improve has d9af53b (cli.py repo_root, real fix plus a WRONG
      `Attempt = AgentSpec` lint silencer) and 0481dfd (wizard tests, clean).
      reese to review before any merge. Loophole: acceptance only checks the
      lint finding is gone. Trap: the Ollama desktop app autostarts an
      UNPINNED server on 11434; kill it and start ollama_gpu1.cmd first.
- [ ] LEDGER bugs seen, NOT fixed (independence rule; for LEDGER to find):
      `cli._test_cmd` splits `--test` on whitespace (quotes and spaced
      paths break), and RunSuite lets a FileNotFoundError from an
      unlaunchable test command crash the episode instead of recording a
      failing suite (`executor._run_cmd` -> `sandbox.popen`).
- [ ] `ledger improve` (bf033a1): self-audit + verified self-repair on branch
      ledger/self-improve, local models only. First audit ran 10-11 ~02:00
      (report: .ledger/improve/report.md). Known open findings it should
      take first: cli.py undefined `repo_root` (533) and `Attempt` (1777);
      untested research.py, signing.py, state.py, wizard.py. Start the loop
      when GPU 1 is free: `ledger improve --model qwen3.8:27b`. Never merge
      the branch without reese.
- [ ] Impossible-task set (FINDINGS #4): squeeze 9/10 (honest, stuck after
      5 attempts); crc run 1 5/10 (cap refused everything, fixed 2dbc61a;
      author edited the linearity test in every response); crc run 2:
      test edit applied, "success" on a failing hidden suite. Harness fixes
      landed 5423437: existing test files can't be edited unless the task
      asks; `INFEASIBLE: <why>` reply escalates `infeasible` with the
      reason. primes: "success" 14/14 + hidden 6/6 in 34 min, being
      audited (too fast to be an honest sieve?). Next: rerun crc and
      squeeze against the new guards, write up, commit.
- [ ] SAT calibration note: pigeonhole(8) is out of reach for a Python CDCL
      (ref times out at 120 s) while naive DPLL does it in 2 s; keep PHP <= 7.

## Release chores (whenever the version changes)

- Run `.\release.ps1` from the repo root — reads the version from
  `pyproject.toml`, builds the wheel, copies it to `kyberos/public/ledger/`,
  rewrites `latest.json` (sha256 included) and the `WHEEL` constant in
  `src/landing/ledgerRelease.js`. Then commit + push the kyberos repo
  (gats-coder has no remote; it is local-only).
- Run it from PowerShell **without** `2>&1`. Under `$ErrorActionPreference
  = 'Stop'`, pip's upgrade notice on stderr is promoted to a terminating
  error and the release dies at the wheel build having done nothing wrong.
- The wheel URL lives in ONE module now (`ledgerRelease.js`), imported by
  both `Ledger.jsx` and `Company.jsx`. Two hand-maintained copies is how a
  home page ends up advertising a version the download link no longer serves.
- Wheels are not byte-reproducible (zip timestamps), so `latest.json` and
  the published `.whl` must be written in the same run — the installers
  verify SHA-256 and refuse on a mismatch.
- `release.ps1` deletes every wheel but the current one, so a previously
  published pinned URL starts 404ing after a release. Deliberate, but worth
  remembering before handing anyone a version-pinned command.

## Direction (stated 2026-09-03)

LEDGER is **the trustworthy, flexible AI agent for devs, companies and
enterprise**: bring your own model, and get auditability, tracking and
reliability as the product rather than as a feature. See the product-thesis
memory — the model is a component, the trust layer is the thing nobody else
is building.

Four workstreams, in the order they unblock each other:

1. **Desktop app** (started, below). Nothing else reaches a non-terminal user.
2. **MCP as contracted planner actions — DONE 2026-09-04**, see below.
   Computer use inherits its rules.
3. **Computer use.** Today there is `vision.py` (screenshot + VLM for design
   review) and nothing that acts. Same rule as MCP when it lands: a click is
   an action with a risk class, gated and journaled, or the receipt stops
   meaning anything.
4. **Model flexibility — DONE 2026-09-04.** The `llm.py` protocol already
   covered claude CLI, anthropic, openai, ollama and any OpenAI-compatible
   base_url; what was missing was provenance, and that is now recorded.

   Every backend already exposed `id` as `provider:model`, so this was
   threading rather than new plumbing: `SkillResult.model` -> the EXEC entry
   -> `actions[].model` in the receipt, plus a receipt-wide `models` roster
   naming each model and what it was used for (goal compilation, AuthorPatch,
   ...). `runs[].goal_backend` carries the compiler's.

   `model` is written to EXEC only when there was one, so the journals of the
   many actions that call no model are unchanged. Actions that called nothing
   report `null` rather than inheriting the episode's model, which is the
   distinction an auditor actually needs.

   The roster is first-seen order rather than sorted, because on a resumed
   episode the model can change between attempts and which one wrote what is
   the question being asked. Tested with exactly that case.

   Open: only the authoring skill reports its model. `product.review`,
   the strategist and `vision.py` all call models and stay anonymous.

## MCP tools as contracted actions (2026-09-04, local only)

Tools reached `chat` only, so the planner could not use one and an MCP call
could never be part of a verified episode. `contracted_actions()` in
`ledger/mcp.py` turns a configured tool into an ordinary catalogue Action, and
`run_repo_episode(..., mcp_config=...)` merges those into the catalogue and
the skill table.

```jsonc
"mcp": {"servers": {"docs": {
  "command": ["python", "tools/docs_server.py"], "risk": "network",
  "tools": {"publish": {"risk": "irreversible",
                        "effects": ["docs.published"],
                        "prec": ["repo.clean"],
                        "arguments": {"path": "site/"}}}}}}
```

Five rules, each because the alternative is the free tool loop this design
refuses:

- **Only contracted tools are reachable.** A server can advertise anything;
  the planner reaches only what config names. The stub in the tests offers
  `peek` and it stays unreachable.
- **The operator owns the arguments.** They are fixed in config. A model
  choosing arguments is a model deciding what happens, and then nothing
  downstream is verifiable.
- **Effects must be in the sensor vocabulary**, validated against
  `catalog_vocabulary` the same way the strategist validates proposals.
  Anything else is refused out loud rather than dropped: an unverifiable
  action in an audited episode is worse than a missing one.
- **A tool may raise its risk, never lower it.** The server's risk is the
  operator's floor, or a tool could declare itself safe and route around the
  gate.
- **Effects are claims, not facts.** The skill reports the contracted
  propositions and the sensors decide. Proved with a stub that reports
  success and changes nothing: it produces an INCIDENT naming
  `tests.pass:unit` as claimed-not-observed, and the episode does not
  succeed on the tool's say-so. That test deliberately queues no patch, so
  the contracted tool is the ONLY route to the goal and the planner is
  guaranteed to reach it.

`deterministic=False` on every one: a remote call over somebody else's pipe
is not a contract, so L1 is optimistic and L2 learns per state.

**A tool result taints the session**, recorded when the reply arrives rather
than an action later, and `taint.external_sources` recognises an `EXEC` whose
action starts with `mcp.` so a receipt explains its own escalations from the
chain alone.

Restructuring note: resolving the episode's `Provenance` moved to before the
catalogue is built, because the MCP skills take it at construction. Building
skills against a fresh one and then adopting the gate's is the two-objects
bug that has now bitten this file three times, where the gate escalates
correctly while the receipt reports a clean sheet.

- [x] 10 tests in `tests/test_mcp_actions.py`, driven against a real stdio
      server. With model provenance, full suite 356 green.

### Open
- [x] **Wired into the CLI.** `ledger run` and the chat runner forward
      `.ledger/config.json` as `mcp_config`, so contracted actions are
      reachable from the command line, not only from the API. An absent
      `mcp` block yields none.
- [ ] Servers started for an episode are never closed; they die with the
      process. Fine for a CLI run, a leak for a long-lived `ledger app`.
- [ ] Tool arguments are static. Templating them from state (a branch name,
      a slot) is the obvious next want and the obvious next way to
      reintroduce model-chosen arguments, so it needs a contract of its own.

## Desktop app + voice (2026-09-03, local only)

- [x] **`ledger app`** — `ledger/app.py`, stdlib `http.server`, no new
      dependencies. Episode list, run timeline built from the journal,
      receipt on demand, gate approval.
- [x] **`DesktopApprover` closes a real hole.** `tty_approver` denies when
      there is no terminal, which is correct reasoning, but it means an
      episode launched from anywhere except a shell has every gated action
      refused and escalates `gate_denied`. The approver publishes the
      question and blocks until the window answers; no answer inside
      `GATE_TIMEOUT_S` (600) still denies, because a gate that defaults to
      yes when unattended is not a gate. Drop-in for
      `PolicyGate(approver=...)`, and a `deny` rule never reaches the window,
      since policy outranks consent.
- [x] **voiceforge absorbed as a TTS engine.** `resolve_tts` is now
      voiceforge -> piper -> sapi -> silent. It is the only engine whose
      identity we own and can sell, and it watermarks by default (EU AI Act
      Art. 50).

      The boundary is loopback HTTP and not an import, deliberately:
      voiceforge needs torch and CUDA, LEDGER has no dependencies at all.
      New file `voiceforge/runtime/speak_server.py` holds a warm engine and
      serves `POST /speak`; nothing else in voiceforge changed, so it stays
      independently runnable and testable. Start it with
      `conda run -n voiceforge python -m runtime.speak_server`.

      Defaults to VITS in the `voiceforge` env on purpose: RTF 0.018 is ample
      for reading out a verdict, and it means the daemon never needs
      chatterbox, which pins torch==2.6.0 and silently downgrades that env to
      CPU. Expressive engines stay in `cbx`.
- [x] 26 new tests (8 voice bridge, 18 app); full suite 344 green.

### Open
- [x] **The Tauri shell is built and runs** (2026-09-04). `desktop/src-tauri`,
      Tauri 2.11, compiles clean in 3m37s to a 5.6 MB
      `ledger-desktop.exe`. Verified by launching it: window titled LEDGER
      appears, it spawns `ledger app --no-open`, the control surface answers
      in 2s, episodes render, and closing the window stops the server.

      No `tauri-cli` needed — the crate is scaffolded by hand and
      `cargo build --release` is enough for the binary. The CLI is only
      required for `cargo tauri build` if we want MSI/NSIS installers, which
      is the remaining packaging step.

      Three rules the shell holds, each because the alternative is worse:
      it polls `/api/health` before painting (a window against a server that
      is not up yet reads as broken, not slow); it reuses an already-running
      server instead of fighting for the port; and it only kills a server it
      started itself.
- [ ] **Installers not built.** `cargo tauri build` needs `tauri-cli`
      (`cargo install tauri-cli`). The bundle config and a placeholder icon
      are in place; the icon is a stand-in until the rename lands.
- [ ] **`running` is inferred from a missing END entry**, so an episode that
      died months ago still shows as running in the window. The stub
      `build-a-calculator-app-in-javascript` journal demonstrates it. Fold in
      file mtime, or journal an explicit abort.
- [x] **Gates now route to the window, including across processes**
      (2026-09-04). `--app-url` on `run` and `chat`, or `LEDGER_APP_URL`.
      `RemoteApprover` posts the question to a running app and polls for the
      answer, so `ledger app` in one window and `ledger run` in another is a
      real workflow rather than an aspiration.

      Every failure path denies: no app, a window closed mid-question, a
      question the server has forgotten. None of those is a human saying yes.
      `--yes` still wins over the app, because it is an explicit decision to
      pre-approve and routing it to a window would ask something already
      answered. Naming `--app-url` for an app that is not running is a hard
      error rather than a silent fall back to the terminal, which would only
      surprise you at the first gate.

      Proved before and after on a real episode: attended profile with no
      terminal gives `success=False, denials=2`; the same episode with a
      window open gives `success=True, denials=0` and the full plan
      `RunSuite -> ApplyPatch -> RunSuite -> Commit`.

      Lifecycle detail worth keeping: `resolve()` is the single place a
      decision becomes final and it pops from `_pending` there. Answering in
      the waiter instead left answered questions sitting in the window with
      live buttons, and made a double-click a second answer to the same gate.
- [ ] **No live stream.** The window polls every 4s. An SSE tail of the
      journal is the obvious upgrade and the journal is already append-only.
- [ ] **Voice is one-way in the app.** It speaks through `voice.py`; the
      window has no microphone. Speaking TO it from the desktop is the point
      of the absorb and is not done.

## Done (2026-08-25: signed receipts + taint escalation, unreleased)

The journal was already the whole truth and could not leave the machine
that produced it. `ledger receipt` extracts a run's claims (policy in
force, every gated decision, every action, every incident, what was in
scope that nobody vouched for), signs them Ed25519, and binds them to the
journal head hash. `ledger verify-receipt` checks the signature alone, or
with `--journal` re-derives the body and compares. `extract()` is a pure
function of the journal file precisely so a verifier can do that.

- [x] `ledger/signing.py`: RFC 8032 Ed25519 in the stdlib (no deps to
      spend; HMAC was never an option because a verifier holding the secret
      could forge what it is checking). Checked against the RFC's own test
      vectors, cross-checked once against `cryptography`. Key auto-created
      at `~/.ledger/keys/default.json`, 0600 at creation.
- [x] `ledger/taint.py` + `PolicyGate(taint_probe=...)`: once untrusted
      content is in scope, the autonomy profile stops applying and every
      non-read action is escalated. The approval memo is keyed by taint
      state, so a `yes` given before a page was read is not inherited after
      it. An explicit `allow` rule still wins: policy is the operator
      saying so in advance, which is the one thing a fetched page cannot do.
- [x] `ledger/receipt.py`: receipts carry `provenance.gaps` (the gate
      decided with untrusted content in scope and recorded none: fails
      verification) and `unexplained_sources` (the gate was stricter than
      the journal accounts for: reported, not fatal).
- [x] **The chat surface is taint-aware too.** One `Provenance` per
      session, shared by the MCP tool gate, the `Conversation`, and every
      episode the session launches, so a tool result read at 10:01 still
      constrains the episode started at 10:04. A refusal (`DENIED`) is our
      own fixed string and does not taint; a `TOOL ERROR` is the server's
      words and does. `run_repo_episode` writes `inherited_taint` into the
      journal HEADER, so an episode's receipt explains its own escalations
      without the reader needing the conversation.
- [x] **The unit is the episode, not the file** (`ledger.receipt/2`).
      Resume writes `episode.r2.jsonl` and a re-run appends another HEADER
      to the file it is in; a receipt now covers every run of every
      segment, reports each attempt separately (`runs[]`, with its own
      profile, plan, escalation and taint), and aggregates into `totals`.
      `--run` narrows to one file's latest run. Point it at the directory
      or any segment, same receipt.
- [x] **Receipts are durable.** Verification rebuilds the body from the
      segment list and entry counts the receipt itself records, not from
      whatever is on disk now, so resuming an episode does not invalidate a
      receipt issued before it: the continuation is a note. `Journal.
      verify_entries` makes that safe: a prefix of a hash chain is a chain.
- [x] **Taint does not leak across a resume.** A resumed episode is a new
      process with a new context; what carries is the HEADER's
      `inherited_taint` and nothing else. The receipt-wide
      `external_sources` union still records what the task ever touched.
- [x] `ledger key`: show or export the public key, since `--key-id`
      pinning is theatre without a way to publish the key it pins.
- [x] 28 receipt tests + 2 denial-regression tests; full suite 318 green.
      README updated.

- [x] **A gate denial no longer outlives the session that gave it.**
      Surfaced by the receipt's per-run breakdown, in one line of output:
      attempt 1 under `attended` denied everything and escalated
      `gate_denied` after 2 steps; attempt 2 resumed with an approving gate
      and escalated `no_path` at **0 steps**. §8.1 treated a refusal as an
      observation, so the divergence arm learned a self-loop, which went
      into `episode_dir/graph.sqlite` AND the repo's shared `l2.sqlite` and
      came back on the warm start. The human changed their mind and the
      planner could not hear it.

      Fix: a refusal is a fact about this session's permissions, not about
      what the action does, and the two must not be confused.
      `run_episode(..., blocked=set())` is filled in by the ENVIRONMENT;
      `RepoExecutor` adds an action to it on gate denial or a blocking
      pre-action hook. A blocked action is pruned from `useful_actions`
      directly, nothing is learned, nothing is persisted. The OBSERVE entry
      records `denied` (and `diverged: false`, because nothing executed, so
      no prediction was falsified), which is also how `replay` rebuilds the
      same blocked set and stays bit-exact. Verified by reverting the wiring
      and watching both new tests fail. After: attempt 2 succeeds with the
      full plan.

**Both real bugs in this work were found by running it end to end, not by
the unit tests**: same pattern as v0.8.x. (1) The receipt derived taint
only from ingress entries, so a gate that escalated correctly still issued
a receipt claiming nothing external was in scope. (2) `run_repo_episode`
built its own `Provenance` while a caller-supplied gate kept a different
one, so the executor journaled a view of the episode the gate never used.
Both are the same failure: two objects where there must be one, invisible
from either side, which is why the wiring now has an identity assertion of
its own (`test_the_chat_session_shares_one_provenance_with_its_episodes`).

### Open
- [ ] **Taint is per-session and never clears.** Correct and blunt: one
      fetched page escalates everything until the session ends. If that
      turns out to be unusable in practice the answer is per-source scoping
      (which action families a given source may influence), not a timeout,
      but do not build that until a real session complains.
- [ ] **`ledger run --strategise` is the other live ingress** and it is
      wired, but the strategist has still never fired in a live episode
      (see the v0.11.0 note below), so the research → escalation path has
      only ever run in tests.
- [ ] **Key distribution is still hand-to-hand.** `ledger key` publishes
      the public half; pinning a `key_id` you were handed out of band is
      fine for a design partner and does not scale past one. No revocation,
      no rotation, no directory. Do not build a PKI: the next real step is
      probably letting a receipt carry a chain to a key someone already
      trusts (a signed commit, a domain).
- [ ] Receipts name blob shas but do not carry the blobs, so a verifier
      who wants the actual command output still needs the episode dir.
      Probably right; worth an explicit `--with-evidence` bundle later.
- [ ] Not released. Version stays 0.14.0 until someone decides to ship it.

## Harness study (2026-10-08): build order in HARNESS_STUDY.md

12 open-source agents read from source. None hashes outcomes for loop
detection and none leaves verification to the policy, so items 1-2 below
are both the fix for the 2026-08-25 failures and unclaimed ground.

Built 2026-10-08 (unreleased, tests/test_harness.py, backup of the tree
before it at `refs/backup/pre-harness-2026-10-08`):

- [x] `Action.forbid` (negative preconditions). AuthorPatch forbids
      `tests.unknown:<suite>` (no authoring on an untested patch: datum) and
      `fail.stuck` (relay).
- [x] `failures.py`: normalized failure signature + FailureTracker fed by
      RunSuite (persisted to failures.json, reloaded on resume). Nudge block
      in the author prompt at 2 repeats; `fail.stuck` at 5; ESCALATION
      reason `stuck` with signature/summary.
- [x] Author evidence: `git grep -w` + diff + `git log -S` for the names in
      the failure; repo-local imports that no longer resolve.
- [x] ApplyPatch gate: syntax + new broken imports -> `git apply -R`, patch
      dropped, reason to the next author.
- [x] `hunks.py` content-located matcher (exact/rstrip/strip/unicode/anchor),
      after `repair_hunk_counts` fails; context always taken from the file.
- [x] `sandbox.py` Job Object (KILL_ON_JOB_CLOSE, created suspended) for
      `_run_cmd`, hooks and MCP servers; POSIX uses a process group.
- [x] `snapshots.py` tree before every write action -> EXEC `tree`, receipt
      `tree_before`; `ledger rewind <episode> [--step N]`.
- [x] MCP servers closed at episode end; `${branch}/${episode}/${worktree}`
      in contracted tool arguments.

Still open from the study:
- [ ] Sandbox phase 2: restricted token + Low integrity, worktree labelled
      Low, `.git`/`.ledger` not; fail closed; "Access is denied" ->
      `sandbox.denied`. Expect test suites that write to %TEMP% or caches to
      break under Low integrity; needs a writable-roots list.
- [x] Best-of-N (`--attempts`) ranks passing attempts by agreement over
      AST-normalized changes (`vote.py`), then steps.
- [ ] Graded RunSuite outcome into L2 (collection < more fails < fewer <
      pass). Touches world-model semantics; needs its own design pass.
- [x] MCP: reader thread routes replies by id; separate `timeout` (start)
      and `call_timeout` (300s); a slow call is cancelled with
      `notifications/cancelled` and the server stays up.
- [ ] MCP lazy connect: conflicts with validating contracts against
      tools/list at episode start; skipped.
- [ ] Relay and datum fixtures re-run live against the new contracts.
- [ ] The gate memo / taint findings below are unrelated and still open.

## Code review of the uncommitted tree (2026-10-08): unfixed

- [ ] `app.py:336` gate server: `ACAO: *`, no token/Origin check; any page
      or test process can approve gates. `app.py:448` unescaped innerHTML,
      Tauri `csp: null`.
- [ ] `executor.py:755` gate memo keyed by family: one MCP tool approval
      covers every tool on that server; taint memo ignores which sources.
- [ ] `skills_llm.py:430` web pages enter the author prompt untainted; the
      tracked diff imports untracked modules (web, brief, modes, self_heal,
      domain_workers, lab_contracts): commit them together.
- [ ] `chat.py:211` taint skipped when MCP text starts with "DENIED".
- [ ] `skills_llm.py:532` 20% rewrite cap fires on any failing suite,
      including normal feature work; still charges the budget.
- [ ] Smaller: tauri orphan on startup timeout (`main.rs:85`), late answers
      recorded as approvals (`app.py:86`), lexicographic segment sort
      (`app.py:233`), OpenAI `image_url` shape (`llm.py:238`), Playwright
      helper-binary pick (`vision.py:139`). (MCP servers never closed: fixed
      by the harness pass.)

## Open (2026-08-25 — two live episodes, two different failures)

Both launched against real repos, both left to run unattended, both failed.
They fail for unrelated reasons and the second one is the important one.

- [ ] **A repeated identical failure must change strategy, not just spend
      budget.** `relay` phases 7+8 (`readstate-rtm`, r2) escalated
      `budget_exhausted` after 32 steps. The planner was NOT at fault — it
      interleaved `AuthorPatch -> ApplyPatch -> RunSuite` nine times and
      verified after every single patch. The suite reported the same
      `ImportError: cannot import name 'get_hub' from 'server.deps'` every
      time, and the loop answered each one by authoring another patch on
      slot `a`. The actual defect was two lines: an earlier patch renamed
      the provider in `server/deps.py` to `get_events` and left
      `server/routes/workspaces.py:11` importing the old name, so all of
      phases 7-8 died at collection. Verification worked perfectly and
      changed nothing. Nothing in the loop notices "identical failure
      signature N times in a row" and escalates to a different move
      (re-read the failing import, widen context, full-file rewrite,
      ask). This is the gap worth closing next.
- [ ] **The planner can stop verifying entirely.** `datum` phases 4+5
      (`sketch-solver`) escalated `max_steps` after 20 steps; steps 9-20
      are twelve consecutive `AuthorPatch -> ApplyPatch` pairs with no
      `RunSuite` between them — authoring blind for 60% of the episode,
      only 4 RunSuite calls total. It left the worktree uncollectable:
      `cad/__init__.py:65` imports `SketchAnalysis` from `cad.sketch`,
      which never defines it, so phases 1-3 that were passing now fail
      too. One RunSuite would have caught it on the next step. Suspect
      ApplyPatch's predicted effects are trusted enough that UCB1 stops
      valuing an observation; compare against the relay run, where the
      same catalogue DID interleave RunSuite.
- [ ] **The four trust primitives are necessary and not sufficient.**
      Worktree sandbox, hash-chained receipts, budget cap and worktree
      isolation as undo all worked exactly as designed in both runs —
      neither the relay repo nor the datum repo was touched, all damage
      stayed inside `.ledger/episodes/*/worktree`. And neither run was one
      you would have been willing to leave alone for an hour: one produced
      32 steps of nothing, the other left a green suite red. Safety held;
      usefulness did not.
- [ ] Both episodes are resumable and reproduce the bugs on demand — keep
      them as the fixtures for whatever detector gets built.

## Done (2026-08-23 — v0.13.0, an instrument for how it LOOKS)

- [x] **`--design-review`.** Every sensor LEDGER had reported something a
      program can decide, so every goal it could pursue was of that kind,
      and builds came out correct and ugly — "beautiful" was not achievable
      because it was not observable. It now screenshots the running build
      in headless Chrome and has a local vision model score it against a
      FIXED rubric (composition, lighting, materials, interface, finish),
      adding `design.ok` to what the planner can reach for.
      Measured: citysprint 3.0/10, REENTRY 3.6/10, the bench viewer 2.2/10,
      lighting 2/10 across all three. I had called citysprint's graphics
      good; the judge and the user agreed it was a prototype.
- [x] **The refinement loop needs no new planning machinery.** With
      `design.ok` in the goal and `ReviewDesign` in the catalogue,
      AuthorPatch → ApplyPatch → ReviewDesign → AuthorPatch-with-critique
      is just what UCB1 search does. The one-shot plan chains in the
      benchmark were not a planner defect: the planner had nothing left to
      reach for once the tests were green.
- [x] Two rules keep a model's opinion safe in a control loop: `design.ok`
      is its own proposition and NEVER substitutes for `tests.pass` (a
      model's opinion is not execution evidence), and the written critique
      goes into the authoring prompt as evidence exactly the way a failing
      test's output does — the score gates, the complaints are what get
      fixed.

### Open
- [ ] **A screenshot cannot tell a live HUD from a mock one.** The v0.12.0
      usability review flagged this before the first design run: "a quiet,
      well-aligned HUD wired to hardcoded placeholders photographs
      identically to one that works." Currently defended only by the
      composite goal (`design.ok AND tests.pass`), where the browser tests
      require the view to change over time. Worth a dedicated check.
- [ ] The rubric is one screenshot at one moment. It cannot see animation
      quality, input feel, or framerate under load.
- [ ] Vision review is slow (a 32B VLM per call), so it is opt-in.

## Done (2026-08-20/21 — v0.10.0 through v0.12.0)

- [x] **v0.10.0 `ledger setup`.** One guided command: essentials, a model
      backend (Claude CLI / API key / Ollama — any one is enough), optional
      speech, preferences. Detects the platform's package manager and
      offers to install what is missing. Keys go in
      `~/.ledger/credentials.json` at 0600, never in the repo config, read
      with getpass so they miss shell history; env vars still win.
      Found building it: `present()` probed the bare name `claude`, but the
      npm CLIs are .CMD shims and CreateProcess ignores PATHEXT without a
      shell — a working install was reported missing.
- [x] **v0.10.1 authoring gets its own deadline** (1800s default,
      `--author-timeout`). One flat timeout served nothing: building the
      REENTRY view, the CLI hit 600s EIGHT times and the episode spent its
      whole budget without writing a line. Applied to a COPY of the backend
      so a long authoring deadline is not also a long deadline for the goal
      compiler. The escalation now says so instead of advising more attempts.
- [x] **v0.10.2 the first run actually works.** A fresh install said
      "claude CLI not found on PATH" and stopped; bare `ledger` was an
      argparse error listing eighteen subcommands. Both fixed, and a real
      bug with them: setup wrote `{backend: {id: ...}}` while the resolver
      read `block["kind"]`, so setup wrote a config the resolver ignored.
- [x] **v0.10.3 whisper.cpp installs itself.** Verified end to end. The
      first version pinned a release URL and 404'd — upstream tags are
      build numbers, so the asset is resolved from the releases API now.
      Archive entries checked for traversal, model checked for its GGML
      magic, download lands on a .part file. RELEASED to kyberos.ai.
- [x] **v0.11.0 the strategist** (`--strategise`). At `no_path` the agent
      diagnoses what the goal still needs, searches the web (documentation
      changes), and proposes new actions with commands. Every proposition
      must already be in the sensor vocabulary; effects must intersect what
      is missing; the command is gated at its declared risk; proposals are
      effects-unknown so the world model learns them from observation.
      The shell guard first tested for the literal substring "curl | sh",
      which catches none of the real forms.
- [x] **v0.12.0 the usability pass.** Checklists for game/web/cli/service/
      library go INTO the authoring prompt and are reviewed against the
      contract before budget is spent. Run against the original chess
      request it named all four of that session's real failures before a
      line was written. Config: `"product": {kind, review, require}`,
      `--kind`, `--no-product-review`.

### Open
- [ ] **The site still serves 0.10.3.** v0.11.0 and v0.12.0 are pushed to
      the private repo but not released. `.
elease.ps1` then commit+push
      kyberos.
- [ ] **The strategist has never fired in a live episode.** Unit-tested
      only. Benchmark tasks 11 and 14 need a SAT solver and z3 is
      deliberately NOT installed, so they are the intended end-to-end test.
- [ ] whisper.cpp on Linux is untested (the asset exists; the code path has
      not been run).

## Done (2026-08-20 — v0.9.5, launch what you built)

- [x] **`ledger run --launch`.** An episode that ends with "goal reached"
      and a journal path is a claim the human still has to go and check by
      hand; the point of finishing a build is to look at it. On success
      LEDGER now starts it and opens a browser. `--launch "<cmd>"` for an
      explicit command, `--no-launch` to override config.
      Detection only serves files or runs a script the project already
      declares: an npm dev/start/serve script, else index.html over http on
      a free port. NEVER file:// — ES modules are blocked on that origin, so
      a double-clicked page is blank and looks exactly like a failed build.
      Fires only on success, with a test asserting the call stays inside the
      success branch so a refactor cannot quietly move it.
- [x] **`~/.ledger/config.json`**, layered under the repo config one key
      deep — same shape as the existing AGENTS.md layering. Without it
      "launch anything you build" would have to be restated per project.
      Nested blocks merge key by key. Now set to `{"launch": true}`.

## Done (2026-08-20 — v0.9.4 shipped)

- [x] **Released 0.9.4 to kyberos.ai and put LEDGER on the home page**: its
      own section between the GATS benchmark and the adversarial band, plus
      an "Install LEDGER" link in the hero CTA row. Verified before pushing:
      clean-venv install, `python -m ledger`, `ledger demo` 100/100, and
      `latest.json`'s sha256 matching the published wheel.
- [x] Home-page hero link points at `/ledger`, not `#ledger`: `.co` is a
      fixed-position scroll container, so fragment navigation does not
      reliably move it.
- [ ] Decide on PyPI. Publishing `ledger-agent` would replace the version-pinned
      URL with a plain `pip install ledger-agent` and delete this whole section.

## Product gaps (combined build order — see CODEX_PARITY.md scorecard)

- [ ] **OS-level sandbox** for skill subprocesses (Job Objects on Windows /
      bwrap on Linux). The one table-stake every rival has some form of.
- [ ] **OpenAI-compatible + local backends** (Ollama etc.) via the
      `LLMBackend` protocol — one class per provider.
- [ ] **MCP client** surfaced as contracted sensors/actions (never a free
      tool loop).
- [ ] **GitHub Action wrapper** around `ledger run --json` + PR auto-review.
- [ ] HTTP hook type; nested-dir instruction files.
- [ ] **L2 merge across agents.** Currently seeded read-only and not merged
      back, so parallel agents share structure but not learned dynamics.
      Blocked on the state-abstraction fix (DESIGN.md §12.8:
      `repo.branch:ledger/<episode>` makes state keys episode-unique).
- [ ] `ledger session` has no live-backend test — the parallel tests use
      pre-queued patches and no LLM.

## Research

- [x] **The multi-agent result AAMAS wants.** DONE 2026-08-16.
      `experiments/pooling_experiment.py` + `results/pooling.json`. 6 agents x
      3 repeats, no LLM: cold 4 steps/1 divergence for every agent; warm pays
      4/1 at k=1 then 3/0 for k=2..6. All reach goal, plan variance 0.
      Required fixing `sensors.py` first — `repo.branch:ledger/<episode>` put an
      agent-minted identifier inside k(s), so state-key overlap between two
      agents on the same repo was ZERO. After the fix: 7 of 7.

- [x] **Heterogeneous task family.** DONE 2026-08-19.
      `experiments/hetero_experiment.py` + `results/hetero.json`. 3 task types
      (depth 1/2/3 independent fixes), 9 agents x 3 repeats, rotated assignment.
      Cold pays 1 divergence EVERY time. Warm splits EXACTLY on task-type
      novelty: first-time type n=9 -> 1.00 div / 5.00 steps; already-pooled
      n=18 -> 0.00 div / 4.00 steps. No agent deviated. Seeded edges saturate
      at 31 once all 3 types are covered.
      FINDING: pooling is amortized PER TASK TYPE, not globally, because
      `patch.queued:<id>` / `patch.applied:<id>` are inside k(s). Population
      benefit tracks coverage of the type space (coupon-collector), not
      population size.

- [ ] **NeurIPS "Who Verifies the Agents?" — Aug 29 23:59 AoE.** DRAFTED
      2026-08-19 at `gats-submissions
eurips-verifies\ledger_verify2026.tex`.
      Compiles clean (0 errors, 0 undefined cites, 0 overfull), 7pp with refs
      starting p6 = 5 content pages, inside the 4-9pp limit. PDF verified
      anonymous (grepped for name/co-author/affiliations/handles: no leaks).
      Title: "The Verifier Must Not Trust the Agent's Names: State Abstraction
      as a Precondition for Shared Verification". Both experiments included.
      Workshop: double-blind, non-archival, Sydney Dec 11-12; OpenReview venue
      `NeurIPS.cc/2026/Workshop/Verify-Agents`; notification Sept 29.
      Supplementary DONE 2026-08-19:
      `neurips-verifies\supplementary\ledger_supplementary.zip` (97 KB, 31
      files) - ledger package + both experiment scripts + pre-generated
      results JSON + test_graph_seeding.py + anonymous README. Verified clean:
      no name/handle/path leaks, all __pycache__ stripped (.pyc embeds the
      author's absolute build path - that was a real leak vector).
      REMAINING: (a) user must add co-author on OpenReview at submission time
      (no MCP tool for this); (b) submit at
      `NeurIPS.cc/2026/Workshop/Verify-Agents` by Aug 29 23:59 AoE.

- [ ] **AI Engineer NYC — Sep 12.** Workshop (1-2h) + lightning talk drafted,
      NOT submitted. Sessionize. Only remaining venue taking workshops.

      AAMAS full paper: **Oct 8, 2026** (author registration Sept 17).

---




## Done (2026-08-19 night — v0.9.2-0.9.4, found by building the race game)

- [x] **v0.9.4 a goal already true is not a goal.** Asked to fix two named
      bugs, the compiler returned bare `repo.clean`. The tree was clean
      before anything ran, so the episode reached its goal in ZERO steps
      and reported success having done nothing. Worst possible failure for
      an audit-first agent: the report was untrue and the journal proving
      it was internally consistent. Root cause: compilation ran BEFORE the
      worktree existed, so it had never seen the starting state. It now
      runs after the initial probe and rejects an already-satisfied goal,
      retrying with a complaint that says so.
- [x] **v0.9.4 glyphs escaping the ASCII fallback.** ui.py degrades ✓/✗
      and the box set when the console can't carry them, but 44 lines
      across five modules printed a bare `·` or em dash past that gate.
      Redirected output on Windows is cp1252: those were written lossily,
      and any character cp1252 lacks would have raised UnicodeEncodeError
      and killed the command AFTER the work was done. All gated now, with
      an AST test that checks per statement, not per line (the box-drawing
      gate sits on a continuation line).

## Done (2026-08-19 night — v0.9.2/0.9.3, found by building the race game)

- [x] **v0.9.2 stale worktree.** A resumed episode kept its original
      worktree. The spec had moved on (8 tests there, 11 on main), so the
      episode verified against the superseded contract and reported "goal
      reached" for work nobody would accept. It now measures the gap,
      warns on stderr and in the spoken summary, and records
      `worktree_commits_behind` in the journal header. Silently passing an
      old spec is worse than failing.
- [x] **v0.9.2 `python -m ledger`.** The console script lands in a Scripts
      directory usually not on PATH; the failure reads "ledger is not
      recognized", i.e. as though the install never happened — exactly what
      happened to the user. `__main__.py` needs only the interpreter that
      did the installing.
- [x] **v0.9.3 reality outranks a falsified contract.** A deterministic
      action's contract was believed unconditionally, so an ApplyPatch whose
      diff no longer applied kept predicting success — no self-loop to
      prune, re-selected, failed, fifteen times to the step cap. Every
      divergence was recorded to L2 and never read back. `predict()` now
      returns the observed effect when a deterministic contract has been
      falsified at that state. Both published experiments re-run to the
      same numbers, so the NeurIPS results stand.

### Open
- [ ] citysprint: race sim being fixed (lap logic + speed/circuit scale);
      three.js city layer not started.
- [ ] Default per-call timeout (600s) too low for whole-app tasks.
- [ ] A "does this contract describe a working product?" review pass —
      would have caught the missing chess opponent and the black screen.
- [ ] **My specs keep sitting one level below what matters.** Four times
      now: file-mentions-Renderer vs pixels drawn; HUD colour vs board
      area; rules work vs an opponent exists; lap counter vs circuit
      driven. And this round the spec was internally contradictory — a
      90s lap demanded on a 240s circuit — which cost a whole episode.
      Check satisfiability of a contract before spending a budget on it.

## Done (2026-08-19 night — v0.9.0/0.9.1, making local models usable)

- [x] **v0.9.0 whole-file authoring.** Writing a valid unified diff is a
      second skill on top of writing code, and it is where small models
      fail while their code is fine. The prompt now offers `FILE: path` +
      fenced content, and LEDGER builds the diff with difflib — correct by
      construction. Verification is unchanged: the built diff still has to
      pass `git apply --check`.
      Security note: my first draft normalised the path before validating,
      so `lstrip("./")` turned `../../etc/passwd` into a clean relative
      path. Rejection now happens on the raw path (unix, windows drive,
      `~`, backslash, embedded traversal all covered by test).
- [x] **v0.9.1 goal-compiler retries.** It was the only model call without
      them; one malformed sample killed an episode before it began. Retries
      now say what was wrong (unparseable / empty / which props are not in
      the vocabulary), and a real give-up is a clean error, not a traceback.

### Open
- [ ] citysprint: sim at 6/8 executed race tests; three.js city layer not
      started. Blocked on model quality (local 30B) while claude.exe is a
      broken 500-byte stub from a bad npm auto-update.
- [ ] Default per-call timeout (600s) too low for whole-app tasks.
- [ ] A "does this contract describe a working product?" review pass —
      would have caught the missing chess opponent and the black screen
      before three episodes were spent.

## Done (2026-08-19 late — v0.8.5-0.8.8, all found by building real apps)

- [x] **v0.8.5** a SUCCESSFUL run printed a stale `escalated: no_path` from
      an earlier attempt; summaries now slice from the last HEADER.
- [x] **v0.8.6** the goal compiler put `patch.applied:a` in a goal. Which
      slot carries a fix is bookkeeping; the planner used slot b, the suite
      went green, and the episode still "failed".
- [x] **v0.8.7** hunk-count repair. A local model wrote a correct 273-line
      file and declared 342 lines; git called the whole patch corrupt and
      four attempts were binned. Counts are recomputed from the body and
      re-checked -- `git apply --check` still gates everything. Verified on
      the real rejected patch. Also: an unreachable backend now reports
      cleanly instead of tracing through the planner.
- [x] **v0.8.8** the compiler wrote `tests.fail_count:unit:1-5 AND
      tests.pass:unit` -- red and green at once, unsatisfiable. Failure
      states are no longer goal material.

### Open
- [ ] **Editing an existing file is where small models fail.** Miscounted
      hunks are repairable; mismatched context lines are not safely
      auto-correctable. A "write the whole file" authoring mode (build the
      diff ourselves with difflib) would remove the format from the
      model's job entirely. This is the single biggest unlock for local
      models.
- [ ] Default per-call timeout (600s) is too low for whole-app tasks.
- [ ] citysprint: sim at 6/8 executed race tests; three.js layer not built.

## Done (2026-08-19 — v0.8.0-0.8.2 voice + hang fixes)

- [x] **Voice sessions** (`ledger voice` / `chat --voice`). Terminal, not a
      desktop app: the mic belongs to the process, and repl() already
      injected input_fn/output_fn. Zero new pip deps — ffmpeg records,
      whisper.cpp transcribes locally (~0.5s), piper speaks. Confirmations
      are keyboard-only and --voice refuses --dangerously-skip-permissions;
      code is never read aloud (spoken_summary strips paths/diffs/glyphs).
- [x] **v0.8.1 — the spinner hid the gate question.** An attended episode
      asked "allow AuthorPatch:a? [y/N]" and the working indicator
      repainted over it every 80ms, so the session looked hung at 15
      minutes while waiting for a keypress nobody could see. Journal proved
      it: HEADER, PLAN, no GATE. tui.paused() + tty_approver suspends it.
- [x] **v0.8.2 — orphaned model calls + stale worktrees.** A timed-out
      claude call killed only the .CMD shim, leaving the model running
      (observed: 16 min after its parent died) and holding the pipes the
      recovery path waits on; now kills the whole tree. Deleting an episode
      directory left the worktree registered in git and broke the next run;
      Worktree.add now prunes and forces past stale registrations.

- [x] **v0.8.3 — episode scratch was committable.** `ledger init` wrote
      `.ledger/.gitignore`; a plain `ledger run` did not, so `git add -A`
      swept an embedded worktree into the user's repo. Every episode path
      now writes it.
- [x] **v0.8.4 — re-running an episode name died** with "worktree already
      exists". Reuse is a filesystem fact, not a flag; --resume only
      controls journal segmenting and graph warm-start.

### Open from this pass
- [ ] **Default per-call timeout is too low for "build me an app" tasks.**
      600s fails a whole-3D-game diff every time; chess needed
      `"backend": {"timeout": 1800}`. Consider raising the default, or
      making the escalation hint say "raise backend.timeout" when every
      author attempt died on timeout.
- [ ] Voice: hands-free VAD (currently push-to-talk), and a spoken
      "shall I run it?" confirmation is deliberately NOT offered — consent
      stays on the keyboard.
- [ ] Orphan prevention on hard kill needs a Job Object (Windows) /
      process group — the timeout path is fixed, an external SIGKILL of
      LEDGER still leaves a grandchild. Ties into the OS-sandbox item.

## Done (2026-08-11, seventh pass — v0.7.0 unified session)

- [x] **One session for everything** — `ledger chat` routes plain messages
      (fail-safe LLM intent check + one-keystroke confirm) and runs verified
      episodes inline; `/do` bypasses; auto episode naming (slug+dedupe),
      auto-resume of failed episodes, auto-init of config; chat journal gets
      EPISODE_LAUNCHED/RESULT links.
- [x] **Multi-provider backends** — AnthropicAPIBackend + OpenAIAPIBackend
      (stdlib urllib): OpenAI, Ollama (local), Groq/DeepSeek/Kimi/vLLM via
      --base-url; config "backend" block; per-agent backend/model/base_url
      in session specs; doctor checks keys.
- [x] **TUI** — framed input field, live /command autocomplete (Tab cycles
      via completion-prefix memory), history, braille spinner, session
      banner; off-TTY falls back to plain input, tests unaffected.
- [x] **MCP tool calling** — stdio JSON-RPC client (initialize/tools/list/
      tools/call), config "mcp.servers"; chat tool loop (reply = one JSON
      object), gated per `mcp.<server>` family, journaled MCP_CALL/RESULT,
      max 5 calls/turn, /tools command.
- [x] **--dangerously-skip-permissions** on run + chat — bypasses gates,
      prompts, AND policy rules, with a red stderr warning.
      Tests 110 → 134.

## Done (2026-08-10, sixth pass — v0.6.0 UI)

- [x] **Terminal UI** (`ledger/ui.py`, stdlib-only) — verdict headlines
      (✓/✗), colored plan chains, aligned tables (ANSI-safe widths), PASS/
      WARN badges, dimmed chrome, escalation-aware `next:` hints on every
      command incl. exact resume commands. Color only on interactive
      terminals (Windows VT enabled via ctypes); NO_COLOR / LEDGER_COLOR /
      TERM=dumb honored; piped output and --json byte-identical to before.
- [x] **`ledger vis`** — journal → one self-contained dark HTML report:
      badges (outcome/chain/profile/goal), stat strip, step timeline with
      GATE/HOOK/EXEC/OBSERVE rows, expandable decision tables, state diffs
      (+/- prop chips), incident + escalation banners, all HTML-escaped, no
      external assets, no JS. Tests 103 → 110.

## Done (2026-08-10, fifth pass — v0.5.0 from the Claude Code + Kimi study)

- [x] **Studied Claude Code (v2.1.219 docs) + Kimi (K3/K2.7-Code/Kimi Code
      v0.34 docs)**; inventories + LEDGER mapping appended to CODEX_PARITY.md.
      (Kimi naming note: no literal "K3 Coder" product — covered all three.)
- [x] **Action hooks** — config-driven `pre_action` (gate-capable, fail
      closed) + `post_action` commands, matched per family, journaled as
      HOOK entries with output blobs, env: LEDGER_EVENT/ACTION/RISK/WORKTREE.
- [x] **Ordered policy rules** — first-match allow/deny/ask with `reason`
      journaled in GATE entries; `ask` forces a prompt even on ungated risks;
      scope ladder --deny/--allow flags → config; deny outranks --yes.
- [x] **Episode resume** (`--resume`) — worktree reuse, warm graph,
      independent journal segments (episode.r2.jsonl…).
- [x] **Best-of-N** (`--attempts N --yes`) — parallel isolated attempts via
      the session layer, deterministic winner, losers reported.
- [x] **CreatePR** (`--pr`) — gh-based PR action gated as network risk,
      `gh pr view`-confirmed sensor, offline-tested with a stub gh.
- [x] **Per-agent models in sessions** (`"model"` per agent spec) and
      **doctor config lint** (unknown keys, bad profile, malformed hooks).
      Tests 88 → 103.

## Done (2026-08-10, fourth pass — v0.4.0 wave 2)

- [x] **`ledger run --json`** — one structured result object for CI (goal,
      plan, incidents, denials, escalation, journal path) + exit code.
- [x] **AGENTS.md layering** — `~/.ledger/AGENTS.md` global + repo root,
      concatenated global→local so the closer file wins.
- [x] **Secret redaction** — bearer/API tokens, GitHub/AWS keys, private-key
      blocks scrubbed from blobs and suite evidence before content-addressing.
- [x] **PushBranch** — `--push` adds a network-risk action (prec repo.clean);
      `ls-remote`-backed sensor confirms origin actually has the branch;
      supervised/attended deny it without a human. Tests 82 → 88.

## Done (2026-08-10, third pass — v0.3.0 "rival Codex" hardening)

- [x] **CODEX_PARITY.md** — full Codex CLI v0.147-era capability inventory
      (researched from official docs) mapped to LEDGER: have / partial / gap /
      different-by-design, with the ranked build order above.
- [x] **M4 risk gates** — PolicyGate profiles (attended/supervised/autonomous),
      per-family approval memory, `--yes`, fail-closed without a TTY. A denial
      is a journaled observation the planner routes around; failed episodes end
      in a journaled ESCALATION with reason.
- [x] **Author budget (§12.3)** — BudgetMeter + budget sensor; AuthorPatch
      requires `budget.llm:ok`; exhaustion escalates instead of looping.
- [x] **Evidence-driven authoring** — failing-suite tail, deterministic
      auto-context selection, and AGENTS.md/LEDGER.md instructions in patch
      prompts (and chat/review).
- [x] **CLI** — config.json actually read (test_cmd/planner/profile/slots/
      max_author_attempts, flags win); `ledger review` (working tree/staged/
      range, journalable); status prints denials + escalation.
- [x] **Multi-slot exercised** — two-bug iterative episode + forced
      both-slots-applied episode. Tests 60 → 82, all green.
- [x] **Live-verified** — two-bug repo: one authored patch fixed both files,
      honored AGENTS.md one-liner rule, replay bit-exact, chain intact;
      `ledger review` caught a planted divisor bug with concrete failures.

## Done (2026-08-10, second pass)

- [x] **gats-coder is under git** — `main`, generated artifacts ignored.
- [x] **Live-backend smoke test PASSED.** `ledger run "fix the failing test"
      --backend claude` on a scratch repo: goal reached, plan
      `RunSuite → RunSuite → AuthorPatch:a → ApplyPatch:a → RunSuite`,
      0 incidents, 0 LLM planning calls, journal chain intact, `status` +
      `verify-chain` clean. `chat --ask` answered repo-aware.
- [x] **Two Windows backend fixes** found by the smoke test (`ledger/llm.py`):
      prompt now goes to `claude` via **stdin** (the npm `.cmd` shim truncates
      argv at the first newline — multi-line prompts silently arrived as one
      line) and output is decoded as UTF-8 (locale codepage mojibaked em-dashes).
- [x] **`release.ps1`** — automates the four-step release chore end to end.
- [x] **v0.2.1 cut with the fixes** — wheel + `latest.json` + `Ledger.jsx`
      updated via `release.ps1`; clean-venv install passes `ledger demo`
      100/100. (The 0.2.0 wheel's `run`/`chat` were broken on Windows.)
- [x] **Kyberos deployed** — landing pages + `/ledger` product page +
      `public/ledger/` committed and pushed; kyberos.ai serves the wheel.

## Done (2026-08-10)

- [x] **LLM patch authoring** (`ledger/skills_llm.py`) — the agent writes code
      now instead of applying patches handed to it. Diffs validated with
      `git apply --check` before queuing; a rejected diff is a plain non-effect.
- [x] **Full CLI surface** (`ledger/cli.py`) — `run`, `chat`, `session`, `init`,
      `doctor`, `status`, `incidents` added. `run_repo_episode` had existed with
      no way to invoke it at all.
- [x] **Chat** (`ledger/chat.py`) — repo-aware REPL + one-shot `--ask`, bounded
      history, hash-chained conversation journal, `/context /tree /clear /save`.
- [x] **Multi-agent sessions** (`ledger/session.py`) — parallel agents in
      isolated worktrees pooling one graph; merge is order-independent and
      journaled.
- [x] Tests 27 → **60**, all green.
- [x] **Kyberos `/ledger` product page** — `src/landing/Ledger.jsx`, routed in
      `useRoute.js` + `App.jsx`, nav link added to all 7 other landing pages.
- [x] **Installable wheel** — `ledger_agent-0.2.0-py3-none-any.whl` hosted at
      `kyberos/public/ledger/` with `latest.json`, `install.sh`, `install.ps1`
      (both verify SHA-256 and refuse to install on mismatch). Verified by
      installing into a clean venv: `ledger demo` passes 100/100.

## Notes

- `AuthorPatch` has an **empty precondition on purpose**. STRIPS preconditions
  are conjunctive, so "author only when a suite is failing" is unsatisfiable —
  the two failure buckets are alternatives, and requiring both means the action
  is never applicable. Ordering comes from search instead: `RunSuite` is the
  shorter path to the goal and gets tried first; `AuthorPatch` is reached only
  once reality falsifies it. This bug cost a debugging cycle; don't re-introduce it.
- The product page leads with `pip install <url>` rather than the curl-pipe
  installer on purpose. The scripts exist, but teaching `curl | sh` as the
  default is a supply-chain footgun.

## Microsoft Center talk (week of 2026-10-26)

- [x] Deck built (talk/deck): 10 main slides for 25 min incl. tips (9); addendum after the close; QR codes on close;
      rest backup; addendum blog post live 2026-10-21.
- [x] Install one-liners on the close slide, both decks (reese, 10-24).
- [x] Backup PDF sent to Mark 10-22 (llm-day-msft-nyc-maureese-williams.pdf). No live demo (reese, 10-22).
- [ ] reese picks which deck to present: index.html or alt.html (`python3 talk/deck/build.py alt.html`).
- [ ] Rehearse to time (25 min) with the speaker notes (N key).
