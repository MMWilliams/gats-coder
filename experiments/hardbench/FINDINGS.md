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
