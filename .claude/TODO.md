# LEDGER — TODO

Goal: make LEDGER usable enough to rival Codex, and ship it as a Kyberos product.

Repo: `C:\Users\reese\gats-coder` · Site: `C:\Users\reese\kyberos`

---

## Blocking (do these first)

- [ ] **Put `gats-coder` under git.** It is not a repository. No history, no
      branches, no backup — and `ledger run` cannot operate on its own source
      because the agent requires a git repo. Everything below is riskier until
      this is done.
- [ ] **Live-backend smoke test.** `run` and `chat` have never been exercised
      against the real `claude` CLI — only `FakeBackend`. The logic is proven;
      the integration is not. Do this *before* the site goes live, because the
      product page tells people to run those two commands.
      ```
      ledger init && ledger run "fix the failing test" --backend claude
      ledger chat --ask "what does planner.py do?"
      ```
- [ ] **Deploy the Kyberos changes.** 9 modified files + `public/ledger/` +
      `src/landing/Ledger.jsx` are uncommitted on `main`. Nothing is pushed.
      Deploying publishes a download link under kyberos.ai.

## Release chores (whenever the version changes)

Bumping the version touches four places. Missing one silently breaks install:

1. `gats-coder/pyproject.toml` → `version`
2. `python -m pip wheel . --no-deps -w dist`
3. copy the wheel to `kyberos/public/ledger/`, update `latest.json`
   (`version`, `wheel`, `sha256` — get it from `Get-FileHash -Algorithm SHA256`)
4. `kyberos/src/landing/Ledger.jsx` → the `WHEEL` constant

- [ ] Automate the above into a `release.ps1` / `Makefile` target. Four manual
      steps with a checksum in the middle is a mistake waiting to happen.
- [ ] Decide on PyPI. Publishing `ledger-agent` would replace the version-pinned
      URL with a plain `pip install ledger-agent` and delete this whole section.

## Product gaps

- [ ] **M4 risk gates.** `attended_gate` approves everything. Needed: policy
      gates, dead-end persistence, human escalation. Until this lands, the
      product page's "run it on branches you're willing to throw away" warning
      is load-bearing.
- [ ] **Per-episode attempt budget for `AuthorPatchSkill`.** A persistently
      failing model burns calls with no ceiling. DESIGN.md §12.3 wants this
      surfaced as a budget proposition that forces escalation.
- [ ] **Multi-slot authoring is untested** with more than one slot in a single
      episode. The catalog supports it; nothing exercises it.
- [ ] **L2 merge across agents.** Currently seeded read-only and not merged
      back, so parallel agents share structure but not learned dynamics.
      Blocked on the state-abstraction fix (DESIGN.md §12.8:
      `repo.branch:ledger/<episode>` makes state keys episode-unique).
- [ ] `ledger session` has no live-backend test either — the parallel tests use
      pre-queued patches and no LLM.

## Research

- [ ] **The multi-agent result AAMAS wants.** `ledger/session.py` is the shared
      transition-graph story the paper lacks — pooled verified edges across a
      population of agents. There is still no *experiment*. A measured result
      here (does agent N+1 plan better because agents 1..N ran?) would close
      the venue-fit gap flagged in [[gats-conference-submissions]].
      AAMAS full paper: **Oct 8, 2026**.

---

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
