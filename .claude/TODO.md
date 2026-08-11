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
- [ ] **`ledger vis`** — static HTML render of a journal (plan, UCB tables,
      gates/hooks, incidents).
- [ ] HTTP hook type; nested-dir instruction files.
- [ ] **L2 merge across agents.** Currently seeded read-only and not merged
      back, so parallel agents share structure but not learned dynamics.
      Blocked on the state-abstraction fix (DESIGN.md §12.8:
      `repo.branch:ledger/<episode>` makes state keys episode-unique).
- [ ] `ledger session` has no live-backend test — the parallel tests use
      pre-queued patches and no LLM.

## Research

- [ ] **The multi-agent result AAMAS wants.** `ledger/session.py` is the shared
      transition-graph story the paper lacks — pooled verified edges across a
      population of agents. There is still no *experiment*. A measured result
      here (does agent N+1 plan better because agents 1..N ran?) would close
      the venue-fit gap flagged in [[gats-conference-submissions]].
      AAMAS full paper: **Oct 8, 2026**.

---

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
