# LEDGER — TODO

Goal: make LEDGER usable enough to rival Codex, and ship it as a Kyberos product.

Repo: `C:\Users\reese\gats-coder` · Site: `C:\Users\reese\kyberos`

---

## Release chores (whenever the version changes)

- Run `.\release.ps1` from the repo root — reads the version from
  `pyproject.toml`, builds the wheel, copies it to `kyberos/public/ledger/`,
  rewrites `latest.json` (sha256 included) and the `WHEEL` constant in
  `Ledger.jsx`. Then commit + push both repos.
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
