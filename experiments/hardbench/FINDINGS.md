# hardbench findings

What the hard tasks taught us about the harness, in the order found.

## 1. The small-diff cap blocked every from-scratch implementation (rk45, run 1)

Episode: 19 min, 3 steps, `no_path`. All four authoring attempts (two per
slot) were rejected by the preflight: "integrators.py: 149 changed lines
exceeds the 24-line limit for a localized test failure". The module was a
15-line stub, so any implementation is a rewrite, and the cap of
`max(24, 20% of the file)` forbade all of them. With both slots' AuthorPatch
recorded as failures the planner correctly found no path and escalated with
4 of 10 attempts used.

Fix (`skills_llm._localized`): the cap now applies only when the suite tail
shows passing tests, since preserving passing behaviour is what it protects.
A suite with nothing passing is being built, not fixed.

Open: the planner gives up after two failed attempts per slot even when the
attempt budget is 10. That is the L2 edge statistics doing their job, but a
preflight rejection is not evidence about the world; it may deserve its own
outcome class.

Run 2 (after the fix): success in 10 min, one authoring attempt, 7/7
visible, 8/8 hidden.

## 2. A hidden oracle that encodes an algorithm choice is not an oracle (hf)

Success in 10 min, one attempt, 6/6 visible, 6/7 hidden. The miss was the H4
square (4 electrons): model -1.6017 Ha, reference -1.6586 Ha. Not a bug. The
square has a degenerate HOMO, so RHF has several stationary points; the
reference's plain fixed-point SCF from the core guess reaches one, the
model's DIIS reaches another, and the task never fixed the guess or the
algorithm. On a non-degenerate chain and rectangle the model's orbital
energies match the reference to four decimals. The hidden test now uses
those geometries; re-graded 7/7.

Lesson for the bench: calibrate hidden tests against two independent
implementations, not one. A value only one implementation produced may be a
property of that implementation.
