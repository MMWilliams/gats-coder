# LEDGER — TODO

Goal: the trustworthy, bring-your-own-model coding agent; auditability,
tracking and reliability are the product. Ship it as a Kyberos product.

Repo: `C:\Users\reese\gats-coder` (origin github.com/MMWilliams/gats-coder)
· Site: `C:\Users\reese\kyberos` · Done history + design notes:
`TODO-archive-2026-09-27.md`.

---

## Next: usefulness (trust held, usefulness didn't)

Both 2026-08-25 unattended episodes kept all damage inside the worktree and
still produced nothing usable. Both are resumable; keep them as fixtures.

- [ ] **Repeated identical failure must change strategy.** `relay`
      (`readstate-rtm`, r2): same `ImportError: cannot import name 'get_hub'
      from 'server.deps'` nine times, answered by nine more patches on slot
      `a`; real defect was a stale import at `server/routes/workspaces.py:11`.
      Detect "same failure signature N times" and escalate to a different
      move (re-read the failing import, widen context, full-file rewrite, ask).
- [ ] **Planner can stop verifying.** `datum` (`sketch-solver`): steps 9-20
      were AuthorPatch/ApplyPatch with no RunSuite, leaving a green suite red
      (`cad/__init__.py:65` imports undefined `SketchAnalysis`). Suspect
      ApplyPatch's predicted effects are trusted enough that UCB1 stops
      valuing observation; compare with `relay`, which did interleave.
- [ ] **Check contract satisfiability before spending budget.** A 90s lap on
      a 240s circuit cost a whole episode. Related: specs keep landing one
      level below what matters (file mentions a Renderer vs pixels drawn).

## Release

- [ ] **Site serves 0.10.3; repo is 0.14.0** (strategist, usability pass,
      signed receipts, taint, MCP actions, desktop app all unreleased).
      `.\release.ps1`, then commit + push kyberos.
- [ ] Decide on PyPI (`ledger-agent`), which would replace the pinned-URL
      install.

## Unproven live

- [ ] **Strategist has never fired in a live episode.** Benchmark tasks 11
      and 14 need a SAT solver (z3 deliberately not installed); they are the
      intended end-to-end test. Same gap covers `run --strategise` research ->
      taint escalation.
- [ ] `ledger session` has no live-backend test.
- [ ] whisper.cpp on Linux never run.

## Desktop app

- [ ] Installers: `cargo install tauri-cli`, then `cargo tauri build`.
      Icon is a placeholder.
- [ ] `running` is inferred from a missing END entry, so dead episodes show
      as running (see `build-a-calculator-app-in-javascript`). Use mtime or
      journal an explicit abort.
- [ ] Window polls every 4s; SSE tail of the journal instead.
- [ ] No microphone in the window; voice is output-only.

## MCP

- [ ] Episode-started servers are never closed (leak under `ledger app`).
- [ ] Arguments are static. Templating from state needs its own contract,
      or it reintroduces model-chosen arguments.

## Trust layer

- [ ] Only AuthorPatch reports its model; `product.review`, the strategist
      and `vision.py` are anonymous in receipts.
- [ ] Taint is per-session and never clears. If a real session complains,
      scope per source; not a timeout.
- [ ] Key distribution is hand-to-hand (no rotation/revocation). Next step is
      chaining to a key already trusted (signed commit, domain), not a PKI.
- [ ] Receipts name blob shas without carrying blobs; maybe `--with-evidence`.

## Product gaps (see CODEX_PARITY.md)

- [ ] **OS-level sandbox** for skill subprocesses (Job Objects / bwrap).
      Also fixes orphaned grandchildren after an external hard kill.
- [ ] Computer use: every click an action with a risk class, gated and
      journaled, same rules as MCP. Today `vision.py` only looks.
- [ ] GitHub Action wrapper around `ledger run --json` + PR auto-review.
- [ ] HTTP hook type; nested-dir instruction files.
- [ ] L2 merge across agents (seeded read-only today). Blocked on DESIGN.md
      §12.8 state abstraction.

## Vision review

- [ ] A screenshot can't tell a live HUD from a mock one; only the composite
      goal (`design.ok AND tests.pass`) defends it. Needs a dedicated check.
- [ ] One screenshot can't see animation, input feel or framerate.
- [ ] Slow (32B VLM per call), so opt-in.

## Smaller

- [ ] Voice: hands-free VAD (push-to-talk today). Consent stays on the
      keyboard; no spoken confirmations.
- [ ] citysprint: three.js city layer not built.

## Research

- [ ] NeurIPS "Who Verifies the Agents?" workshop: notification Sep 29.
      Confirm it was actually submitted (draft + supplementary in
      `gats-submissions\neurips-verifies\`).
- [ ] AAMAS full paper due **Oct 8, 2026** (pooling + hetero experiments done).

---

## Release chores (whenever the version changes)

- Run `.\release.ps1` from PowerShell **without** `2>&1`: under
  `$ErrorActionPreference = 'Stop'` pip's upgrade notice kills the build.
- Wheel URL lives only in `src/landing/ledgerRelease.js`.
- Wheels aren't byte-reproducible, so `latest.json` and the `.whl` must come
  from the same run (installers verify SHA-256).
- `release.ps1` deletes old wheels, so version-pinned URLs 404 after a release.

## Notes

- `AuthorPatch` has an **empty precondition on purpose**. STRIPS
  preconditions are conjunctive, so "author only when a suite is failing"
  is unsatisfiable; ordering comes from search instead. Don't re-introduce it.
- The product page leads with `pip install <url>`, not the curl-pipe
  installer, on purpose: teaching `curl | sh` is a supply-chain footgun.
