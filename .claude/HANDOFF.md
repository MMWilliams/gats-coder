# HANDOFF: LLMday NYC talk prep (2026-10-26, late)

## Goal
Talk "What Did Your Agent Actually Do?" ready to give Thu Oct 29, 2026, 3:30 PM
EDT, main room, Microsoft Experience Center NYC, 677 5th Ave (25 min + 5 Q&A).
Done means: index.html rehearsed to time.

## Status (all pushed, last ee45679)
- Deck: index.html only (alt.html not maintained). 14 main slides, end 23:00
  (2 min buffer): title, about, timeline 2:30, harness->model 4:00, METR
  horizon 5:15, 7 gaps 6:45, unlocks 8:45, GATS 10:00, limit 11:15, LEDGER +
  record 12:00, evidence 14:30, where + habits 16:30, predictions 19:30,
  the CLOSE (reese 10-26) "We can only prompt what we can foresee" 21:00, then
  slide 15 "Thank you" + QR codes + install lines (23:00, up through Q&A).
  Old problem slide is the first backup.
- Generators: `python3 talk/deck/timeline.py index.html` (run from
  talk/deck) and `python3 talk/deck/horizon.py index.html` swap their SVGs in
  place. horizon.py reads talk/deck/data/metr_horizon_v1_1.yaml (METR TH1.1).
- Every number on slides 5 and 6 is verified and listed with its source in
  MICROSOFT_TALK.md "Numbers you can say".
- Slide 7 ends with "Prompts scale with our foresight. Plans scale with the
  agent's, and the record lets us check it." (326f57f); its notes carry
  commander's intent and the METR dev RCT (19% slower, believed 20% faster).
- Sailor analogy (dead reckoning vs a chart that gets corrected) is in the
  GATS slide notes. Intern line ("forgets the project every morning and
  grades their own homework") is not placed yet.
- Backup PDF sent to Mark 10-22 predates all 10-26 slides. Current full deck PDF
  (48 pages) is talk/deck/out/llm-day-msft-nyc-maureese-williams-v2.pdf
  (sent to reese 10-28 with the GATS arXiv v1 and the LEDGER workshop draft,
  gats-submissions/neurips-verifies/ledger_verify2026.pdf).
- Antigravity (Google, Nov 2025) named wherever Claude Code and Codex are:
  timeline ACT lane, notes, both backup tables, MICROSOFT_TALK Q&A. Its
  Artifacts (plan, walkthrough, screenshots, browser recordings) = closest to a
  record, but agent-written and unchecked.

- 10-28 brainstorm: guiding always-on agents = ship's watch (standing orders,
  call-me conditions, logbook, watch handover). Offered for close-slide notes;
  reese hasn't answered.
- 10-28 brainstorm: harness era / harness laws (see TODO). Not on slides yet.

- 10-28: plain-language pass (no slogans: 'trust not talent', 'moat', 'honest',
  'lies', 'unlock' gone). LEDGER framed as a zero-trust experiment (slide 10:
  it can't set state, claim success, pick or run actions). Slide 11 is now
  'What zero trust bought': LEDGER vs most coding agents (published numbers,
  labeled not head-to-head) + crc before/after. Old hardbench table is backup.
  Full PDF: talk/deck/out/llm-day-msft-nyc-maureese-williams-v3.pdf.

- 10-28 YC-style declutter (Kevin Hale: legible, simple, obvious): slides 2, 3,
  4, 6, 7, 9, 10, 11, 12, 14 cut to one idea, bigger type; detail moved to
  notes. Notes are now the spoken script ([brackets] = stage directions).
  talk/deck/script.py exports talk/SCRIPT.md (runs at the end of build.py):
  ~16:15 spoken at 140 wpm, no slide over its slot. PDF v4 in out/.

- 10-28 Sequoia pass: purpose sentence on slide 1; agenda and main eyebrows follow
  What's happening / Why now / So what / What I built / Proof / What's next
  (Sequoia template + AI Ascent 'What? So what? Why now?'). PDF v5 in out/.

- 10-28 practice script: main slides retimed to ~2 min per content slide
  (0:00, 0:45, 1:45, 3:45, 5:30, 7:45, 10:00, 11:30, 13:30, 14:30, 16:30, 18:30,
  20:45, 22:00 close, 24:15 thank you). Each main slide's notes = <ul
  class="beats"> (points to hit in own words) + <p> script. Presenter view (N)
  renders the list. SCRIPT.md has Beats + Script per slide: ~20:04 spoken,
  content slides 1:15-1:59 each, none over. New research beats: CoT/ToT/
  Reflexion/LATS numbers, R1-Zero 'wait', Goodhart, METR method + Project Vend,
  tau-bench pass^k, batch-size nondeterminism, AlphaGeometry vs 10, Vafa NYC taxi
  map (1% closed: ~100% to 67%), 20^10 vs 5^4, CRC-32 affine, ship's watch.
  Not yet in the numbers table: CoT/ToT/Reflexion/LATS, Vafa, AlphaGeometry 10.
- 10-29: jokes removed (reese: 'be charming instead'). Warmth added: thanks to
  LLMday/Microsoft, why I started (slide 2), audience credit (3), METR credit (5),
  co-author + taxi rides past this building (8), embarrassed and glad (11),
  habits I use (12), invite to try or break LEDGER (14), hard questions (15).
  Script ~20:46 spoken, none over.
- 10-29 (reese): show-of-hands questions are their own slide 3 'Who remembers AI
  before...' (reasoning 2024, chain of thought 2022, retrieval 2020; fix outside
  the model -> what it unlocked; 'Each jump in usability and trust started outside
  the model.'). Slide 1's Claude Code hands question removed. Timeline (now 4)
  keeps lineage + harness/scale/RL coupling. 16 main slides, all retimed; close
  21:30, thank you 23:40. Script ~21:01, none over. PDF v6 in out/.
- 10-29: slide 3's black bar removed. Animations: arrow keys (also PageUp/Down,
  clicker, click) step through .frag reveals grouped by data-step before changing
  slides; back steps undo, back from a slide's start lands on the previous slide
  fully revealed. Entrance rise on every slide, left-to-right wipe on the timeline
  and METR chart, GATS bars grow on reveal. [→] cues in each script mark presses;
  bottom-right label shows 'step n/N'. build.py loads ?all so PNG/PDF show every
  step. horizon.py wraps the responsibilities band in a step. PDF v7.
- 10-29 charts native + interactive: timeline.py and horizon.py emit a-* hooks
  (a-fade/a-pop/a-grow/a-grow-up/a-drop, delay --d, duration --t; CSS in deck) so the
  timeline plays 2020->2026 and METR points pop in release order, then the fit and
  projection draw. Hover tooltips (data-tip) on every milestone/point/bar/loop node;
  hover a timeline lane to focus it. METR 50%/80% toggle (points move to p80).
  GATS bars: stress test vs live Llama 3.2 toggle + step 5 (counts animate). Close
  loop: token circles SENSE->COMPARE->PLAN->ACT. Clicks on charts don't advance.
  PDF v8; script ~21:04, none over.
- 10-29 (reese): pop-culture references dropped. The David slide was reverted (d2e921a);
  deck + SCRIPT.md are the version reese emailed to self at 9:01 (= 564d66f, 16 main
  slides, close 21:30, thank you 23:40, script ~21:04). PDF v10.
- 10-29: talk/TLDR.md, a technical TL;DR per slide (concepts + message). Update it with slide edits.
- 10-29: talk/CHEATSHEET.html, iPhone cheat sheet (per-slide message, opener, points, press counts, terms,
  numbers, always/never say, Q&A, practice clock); phone PDF deck/out/cheatsheet-iphone-v1.pdf. Press counts
  verified against the deck (scratchpad cheat_check.py). Hand-written: update with slide edits.
- 10-29: explained L1/L2/L3, world model, state-transition graph from gats-submissions/arxiv-v2/main.tex sec 3 (chat only).
- 10-29: master summary essay (NYT-style narrative, ~2,600 words + glossary) as a Claude Doc:
  https://claude.ai/artifact/UKxZtYAxD8uWPqjgNeVLEe ("What Did Your Agent Actually Do?"). Hand-written: update with slide edits.
  Kindle PDF: talk/ESSAY.html (4.5x6 in, serif, e-ink) -> deck/out/essay-kindle-v1.pdf (24 pages). Doc and HTML are
  separate copies: re-sync if either is edited. Both synced 10-29 to the 19-slide deck (doc rev 11, Kindle PDF v2, 27 pages).
- 10-29 (reese): new slide 16 "Clear the context. Keep the state." (23:15-24:25) before the QR slide:
  hands (lost the plan? rot or bloat? what is a handoff file?) + HANDOFF.md card; close slide now 21:15-23:15 and its
  LEDGER invite + thank-you moved to slide 16; thank you at 24:25. Cheat sheet, TLDR, timing map synced; essay NOT.
  /goal researched (code.claude.com/docs/en/goal): Stop-hook loop + Haiku evaluator on transcript; chat answer only.
- 10-29: slide 16 simplified to "Quick show of hands" (3 big questions, 1 per press + "Write it down. Clear the
  context. Start fresh."), 23:30-24:25. Slide 3 adds 4th question "ChatGPT?" (BERT, GPT-2, T5, GPT-3; Attention Is All
  You Need, Vaswani et al., Google, 2017), 7 presses, slot 1:35-3:10. Script ~21:47, none over. PDF v13, cheat sheet v4.
- 10-29: 19 main slides. New slide 4 "Before chat, there was the notebook" (real outputs, ace-step venv, transformers
  4.57.6: BERT paris .417; DistilBERT-SQuAD on talk title -> Microsoft .265; GPT-2 greedy repeats Q/A). New slide 11
  "Paths merge, dead ends close, the goal holds" (SVG web, stage action s1..s4 via data-act=stage). Trimmed R1 detail
  (absorption), tau-bench (gaps), GATS UCB1 sentence, habits context rule. Slots auto from word counts (scratchpad
  retime2.py): spoken ~22:27, thank you 24:25 (tight: next cuts = Project Vend, ship watch). PDF v14, cheat sheet v5.
- 10-29 (approved): slide 4 "The dark ages (pre-ChatGPT)" with animated SVG caveman ("Running GPT-2, circa 2019");
  slide 11 web denser (31 nodes, 74 strands, 2 merges, 5 dead ends; scratchpad web2.py). Script ~22:29, thank you 24:25.
  PDF v15, cheat sheet v6.
- 10-29 Bing 'Sydney' researched, NOT on slides yet: Feb 2023 drift in sessions of 15+
  questions (Bing blog 'Learning from our first week'); fix in the harness: 5 turns/session,
  50/day, raised to 6, 10, then 30/300 by Jun 1 2023. Offered as a line on slide 7.
- 10-29 external-verifier examples researched, NOT on slides: AlphaProof (Lean checks every
  proof; IMO 2024 4/6, 28/42 silver; manual formalization, up to 3 days), AlphaEvolve
  (evaluators run every program; 4x4 complex matmul in 48 vs Strassen 49), and the
  counterexample Sakana AI CUDA Engineer (Feb 2025: gamed a buggy evaluator, outsiders
  caught it). Offered for slide 9 'If the gaps closed'.

- 10-29 LEDGER paper on the blog (maureeseblog c7bd44f, Nexus release 109, verified live): named
  preprint built from the workshop tex (gats-submissions/neurips-verifies/ledger_verify2026_preprint.tex,
  option preprint, author block = Independent Researcher, New York); PDF at /papers/ledger-verifier-names-2026.pdf,
  post /writing/the-verifier-must-not-trust-the-agents-names, "Paper · PDF" link on /research/ledger, LEDGER
  limits line rewritten to match the paper. Workshop outcome unknown (no OpenReview mail found), so the
  post says "preprint" only.

- Logistics (verified 10-29): talk 3:30 PM EDT, Main room (single track), Microsoft Experience Center, 677 5th Ave
  (53rd-54th). Ticket: Luma, email "Registration confirmed for LLMday NYC 2026 Q4" (10-09, starred, Wallet pass).
  llmday.com bio still says Disney while the listing says Google.

## Decisions and why
- reese 10-26: index.html; LEDGER stays MIT; new slides go first (past and
  patterns), then what's missing, then unlocks, then GATS, then LEDGER.
- Horizon projection uses METR's own post-2023 doubling (129 d, CI 104-158)
  with a least-squares intercept; Mythos (>16 h) shown hollow, excluded from
  the fit, as METR does. Label it "human time", never "runs alone for".
- "Model as last resort" framed as the bet; GATS's planner may be absorbed.

## Approaches that failed
- Edit tool trims trailing spaces in new_string: notes prefixes lost their
  space twice. Use python replace for "M:SS-M:SS. " prefixes.
- Labels near the 2024 marker / 2026-27 cluster collide; fixed by side
  anchoring. The month label still touches the dashed line (acceptable).
- Python heredoc prints crash cp1252 console on arrows: PYTHONIOENCODING=utf8.
- No PyYAML; horizon.py parses the METR yaml with regex.

## Exact next step
reese rehearses from talk/SCRIPT.md beats with a timer (deck clock: T resets)
and reports which slides run long; trim those slides' notes in index.html, then
build.py. Cut habits first if over 25:00.

Done 10-26: LEDGER installs from maureesewilliams.io/ledger (blog 37cc7a9,
Nexus release 106; Windows + WSL verified, tamper refused); close slide and
MICROSOFT_TALK landmine 5 switched; release.ps1 mirrors to the blog. After any
future release.ps1 run, deploy both kyberos (push) and the blog (Nexus).

## Test command
```
python3 talk/deck/build.py   # "47 slides -> ...", ~60-90 s, run in background
```
Result 10-29: "49 slides", "script: ~21:01 spoken", no OVER; presenter notes
checked visually on slide 8 (out/notes-v1b-s08.png); new slide 3 checked (out/hands-v1c-s03.png).
