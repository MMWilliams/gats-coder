# Ledger research laboratory: bounded first implementation

Ledger remains an episode-based coding agent. This addition makes its research
work explicit and measurable; it does **not** demonstrate sustained recursive
self-improvement or grant candidate code authority over its own release.

## Operating modes

- `ledger plan "..." --backend ollama --model gemma4:31b --out design.md`
  generates a design document with architecture, domain contracts, verification,
  milestones, evaluation, and rollback. It makes no code changes. It refuses to
  overwrite an existing document.
- `ledger research "state and prove ..." --domain mathematics --backend ollama --model gemma4:31b` (or `coding`,
  `science`, `reasoning`) asks the domain worker for a provisional candidate and
  records the problem and proposal under `.ledger/research/evidence.jsonl`.
  Assumptions, constraints, input references, a task budget, and a fixed
  evaluation-contract identifier can be supplied as flags. The CLI does not
  label a model answer as verified.
- `ledger goal "..." --accept "python tools/verify_candidate.py" --backend ollama --model gemma4:31b --yes`
  resumes a named isolated episode until both Ledger's own goal and the fixed,
  operator-owned acceptance command pass. The candidate worktree path is passed
  as the command's final argument. Use `--max-iterations N` to bound a session;
  otherwise Ctrl-C stops it. Put the checker in the original repository or a
  separately controlled location, not in the candidate worktree.
  For an exact polynomial task, use
  `--accept "python -m trusted_control.cli polynomial n**2 answer.txt"`.
  Ledger appends the candidate path; the expected expression and checker stay
  outside it. The `science` and `reasoning` subcommands respectively score
  predictions against fixed observations and validate a constraint assignment.
- `ledger loop "..." --backend ollama --model gemma4:31b --yes` continues refining in the episode worktree even
  after a successful episode. Each iteration receives the prior journal path.
  Ctrl-C stops it; `--max-iterations N` bounds a session. The worktree is left
  for inspection. Iteration is not automatic promotion to the primary checkout.

`--name` preserves a stable worktree for resumption; use a fresh name to start
an independent trial. The sidecar `acceptance.jsonl` records external checker
results without breaking Ledger's hash-chained episode journal.

## Contracts and evidence

`ledger.lab_contracts` defines `ProblemSpec`, `Claim`, `CandidateManifest`,
and a hash-chained append-only `EvidenceStore`. Claims retain evidence status:
for example a numerical experiment remains simulation-supported even when its
result looks like a proof. A chain detects edits within a single store but does
not authenticate its writer. Protected evidence needs separate filesystem
permissions, backup, and ideally a different service account.

`ledger.domain_workers` gives coding, mathematics, science, and reasoning
distinct instructions and keeps every proposal provisional. Its external-check
callback is an integration point for real domain runners. The current machine
does not have Lean or Z3 on PATH, and this first implementation does not run a
formal proof checker or physical experiments.
`trusted_control.checkers` now performs exact rational-polynomial identity
checks, held-out numerical prediction scoring against fixed case IDs and an
operator-set threshold, and exact linear-constraint certificate validation.
Those are useful executable subcontracts. A polynomial identity check is not
a proof of an arbitrary theorem; a held-out score is not a physical result.

The `trusted_control.evaluation` package defines fixed domain checks and
rejects missing provenance, unmatched budgets, vetoes, absent mandatory checks,
or insufficient paired gains. Coding requires build, independent tests,
regression, and security checks. Formal mathematics requires a pinned proof
checker, statement fidelity, and an axiom policy. Computational science
requires provenance, controls, uncertainty, and held-out prediction. Reasoning
requires a task certificate and an independent validator. These check names
are contracts for actual external runners; the current package does not
pretend that naming a check executes Lean, a laboratory experiment, or a test.
For shared candidate changes, `judge_portfolio` requires every affected
domain's improvement threshold and every other evaluated domain's regression
tolerance to pass; a coding gain cannot compensate for a mathematics veto.

The paired confidence bound is a screening statistic. It assumes a
prespecified experiment with independent replicates. A production promotion
campaign must predefine task-family splits, account for multiple adaptive
comparisons, limit protected-feedback reuse, and keep an audit split outside
the improvement loop. Permission violations and evidence tampering are vetoes.
The `compare_controllers` check measures the protected quality of successor
agents produced by two research controllers from paired starting agents and
seeds under equal research and task budgets. It is evaluation machinery, not
an assertion that Ledger has already improved its own improver.
`ledger.improvement` records concrete failures, model-proposed hypotheses,
discriminating tests, ablations, and candidate lineage. Its acquisition score
allocates experiments but has no power to approve them. Ledger episodes can
construct candidate code; only the protected evaluator can select a release.

## Model and trust boundary

This machine has local `gemma4:31b` (31.3B), `qwen3-coder:30b` (30.5B), and
smaller Ollama models. The live Ledger trial explicitly selected the largest
model, `gemma4:31b`; use `--backend ollama --model gemma4:31b` on each mode,
or choose `qwen3-coder:30b` for a coding comparison. Model output is a
candidate artifact, never proof.

The first live `gemma4:31b` Ledger self-implementation episode is preserved at
`.ledger/episodes/rsi-foundation-gemma/`. It exhausted its authoring budget
without a passing suite. Its candidate `ledger/lab.py` was not merged: it
rewrote an alleged append-only evidence file and accepted candidate-supplied
boolean fields as proof. This is a useful negative experiment, not a promoted
implementation. The subsequent local changes made test execution resistant to
stale Python bytecode and stopped redundant suite runs on unchanged content.
The live mathematics workspace initially returned malformed output, then
recovered after a bounded retry was added. Its odd-number-sum example remains
`hypothesized`, not machine-checked.

The original repository currently has an uncommitted worktree. Ledger's own
episodes start from committed HEAD and keep generated patches in isolated
worktrees; they do not automatically inherit uncommitted source changes. This
matters when comparing the candidate with the current development state.

Running the evaluator package in the same process or account as a candidate
does not enforce a security boundary. Production isolation requires a separate
process/account with candidate checkout mounted without evaluator credentials,
protected datasets, or release permissions, plus execution quotas and network
policy. Do not promote a candidate based only on its self-reported checks.

## Next research milestones

1. Pin development tasks in four domain workspaces, with actual checker
   adapters: repository tests, Lean proof checking and statement review,
   reproducible computational-science analyses, and executable reasoning
   validators. Keep exploratory science and informal math visibly provisional.
2. Execute candidate variants in isolated, budget-limited worktrees. Capture
   model calls, failed builds, time, memory, dependencies, seeds, and evidence
   provenance. Compare variants on unseen task families at matched budgets.
3. Add a research controller that proposes one intervention and an ablation,
   archives failures, and selects variants only after external evaluation.
4. Test changes to that controller on fresh improvement campaigns: frozen
   baseline, agent-only evolution, recursive evolution, and ablations. Measure
   the quality of successor agents it produces, including unsuccessful work.
5. Add weight or adapter training only as a separately attributed experiment.

This staged boundary preserves an independent answer to the question: did the
change improve future research under a fixed resource budget?
