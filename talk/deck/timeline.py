"""Regenerate the harness-lineage timeline SVG on main slide 4 of the deck.

    python timeline.py index.html   # edit LANES, then rerun; swaps the <svg class="tl"> block in place

The chart plays as a time-lapse when its slide opens (a-* classes, delays in --d, durations in --t; CSS in the
deck): bars grow with a 2020 -> 2026 cursor and milestones pop as it passes. Hover a milestone for what it added;
hover a lane to focus it.
"""
import re
import sys
from html import escape
from pathlib import Path

DECK = Path(sys.argv[1])
BLUE, AMBER, RED, INK2, INK3, SURF = "#2b6cd4", "#a8690c", "#c72d26", "#45464a", "#8a8b90", "#f7f7f8"
W, H = 1376, 460
X0, X1, Y0, Y1 = 170, 1180, 2020.0, 2026.8
SHIFT = 2024.7  # o1 (Sep 2024) to R1 (Jan 2025)
SPEED = 0.5  # seconds of animation per year


def x(year):
    return round(X0 + (year - Y0) / (Y1 - Y0) * (X1 - X0), 1)


def at(year):
    return round(0.35 + (year - Y0) * SPEED, 2)


# name, subtitle, color, fate, bar start, dashed, [(year, label, above, tooltip)]
LANES = [
    ("KNOW", "context", BLUE, "in the harness", 2020.2, False,
     [(2020.4, "prompt, RAG", True, "2020 · Few-shot prompts and retrieval (RAG): put the right text in the context"),
      (2023.8, "MemGPT", True, "2023 · MemGPT: the model pages its own memory in and out"),
      (2025.4, "memory files, compaction", True, "2025 · Memory files (CLAUDE.md, AGENTS.md) and context compaction")]),
    ("THINK", "reasoning", AMBER, "in the model", 2022.0, False,
     [(2022.1, "chain of thought, voting", True,
       "2022 · Chain of thought: PaLM 540B on GSM8K, 18% to 57%. Self-consistency voting: +18 points"),
      (2023.2, "Reflexion", False, "2023 · Reflexion: critique the failed attempt, retry. 91% on HumanEval"),
      (2023.7, "Tree of Thoughts, LATS", True, "2023 · Search over partial solutions. Game of 24: 4% to 74%. LATS: 92.7% HumanEval")]),
    ("ACT", "tools", BLUE, "in the harness", 2022.7, False,
     [(2022.8, "ReAct loop", True, "2022 · ReAct: interleave reasoning and tool calls. +34 points on ALFWorld"),
      (2023.45, "function calling", False, "2023 · Native function calling: tools as structured calls, not parsed text"),
      (2024.3, "SWE-agent, CodeAct", True, "2024 · Agent-computer interfaces for code: edit, run, test in a loop"),
      (2024.9, "MCP", False, "Nov 2024 · Model Context Protocol: one standard plug for tools and data"),
      (2025.6, "Claude Code, Codex, Antigravity", True, "2025 · Frontier coding agents: a simple loop around a reasoning model")]),
    ("CONTROL", "permission", BLUE, "in the harness", 2023.3, False,
     [(2023.3, "AutoGPT asks y/n", True, "2023 · AutoGPT: a human approves every step"),
      (2025.4, "permission modes, sandboxes, hooks", True, "2025 · Permission modes, sandboxes and hooks around coding agents")]),
    ("PROVE", "verify, record", RED, "barely built", 2023.8, True,
     [(2023.8, "SWE-bench: tests as judge", True, "2023 · SWE-bench: real GitHub issues, judged by the repo's own tests"),
      (2026.5, "GATS, LEDGER: sensors, replayable journal", False,
       "2026 · GATS and LEDGER: sensors decide state; a hash-chained journal you can replay")]),
]
LANE_H, TOP = 76, 27
LABEL = 'paint-order="stroke" stroke="#fff" stroke-width="5" stroke-linejoin="round"'


def anim(kind, delay, dur=None):
    t = f";--t:{dur}s" if dur is not None else ""
    return f'class="{kind}" style="--d:{delay}s{t}"'


out = [f'<svg class="tl" viewBox="0 0 {W} {H}" style="width:100%;font-family:var(--sans)">']
bottom = TOP + LANE_H * len(LANES)
lanes = []
for i, (name, sub, color, fate, start, dashed, dots) in enumerate(LANES):
    cy = TOP + LANE_H * i + LANE_H // 2
    g = [f'<g class="lane">',
         f'<rect x="0" y="{TOP + LANE_H * i}" width="{W}" height="{LANE_H}" fill="{SURF if i % 2 else "transparent"}"/>',
         f'<text {anim("a-fade", 0.05 * i)} x="0" y="{cy - 2}" font-family="var(--mono)" font-weight="600" font-size="17" '
         f'letter-spacing="2" fill="{color}">{name}</text>',
         f'<text {anim("a-fade", 0.05 * i)} x="0" y="{cy + 19}" font-size="15" fill="{INK3}">{sub}</text>']
    end_year = SHIFT if name == "THINK" else Y1
    grow = anim("a-grow", at(start), round((end_year - start) * SPEED, 2))
    if dashed:
        g.append(f'<line {grow} x1="{x(start)}" y1="{cy}" x2="{x(end_year)}" y2="{cy}" stroke="{color}" stroke-width="6" '
                 f'stroke-dasharray="14 10" stroke-linecap="round"/>')
    else:
        g.append(f'<rect {grow} x="{x(start)}" y="{cy - 5}" width="{round(x(end_year) - x(start), 1)}" height="10" rx="5" fill="{color}"/>')
    if name == "THINK":
        g.append(f'<rect {anim("a-grow", at(SHIFT), round((Y1 - SHIFT) * SPEED, 2))} x="{x(SHIFT)}" y="{cy - 5}" '
                 f'width="{round(X1 - x(SHIFT), 1)}" height="10" rx="5" fill="{AMBER}" opacity=".28"/>')
        g.append(f'<text {anim("a-fade", at(SHIFT) + 0.4)} x="{round((x(SHIFT) + X1) / 2)}" y="{cy + 28}" text-anchor="middle" '
                 f'font-size="16" font-style="italic" fill="{AMBER}">inside the model now</text>')
    for year, label, above, tip in dots:
        cx = x(year)
        g.append(f'<g class="ms" data-tip="{escape(tip)}">')
        g.append(f'<circle {anim("a-pop", at(year))} cx="{cx}" cy="{cy}" r="8" fill="#fff" stroke="{color}" stroke-width="4"/>')
        if year == 2026.5:
            g.append(f'<text {anim("a-fade", at(year) + 0.1)} x="{X1}" y="{cy + 28}" text-anchor="end" font-size="16" '
                     f'font-weight="600" fill="{color}" {LABEL}>{label}</text>')
        else:
            # keep labels off the dashed 2024 marker: shift any label that would straddle it to one side
            half, m = len(label) * 4.2, x(SHIFT)
            tx, anchor = cx, "middle"
            if cx <= m < cx + half + 6:
                tx, anchor = m - 10, "end"
            elif cx - half - 6 < m < cx:
                tx, anchor = m + 10, "start"
            g.append(f'<text {anim("a-fade", at(year) + 0.1)} x="{tx}" y="{cy - 16 if above else cy + 28}" '
                     f'text-anchor="{anchor}" font-size="16" fill="{INK2}" {LABEL}>{label}</text>')
        g.append("</g>")
    g.append(f'<text {anim("a-fade", at(Y1) + 0.2 + 0.1 * i)} x="1206" y="{cy + 6}" font-size="17" font-weight="700" '
             f'fill="{color}">{fate}</text>')
    g.append("</g>")
    lanes += g
out += lanes
shift_tip = "Sep 2024 to Jan 2025 · o1 and DeepSeek R1: RL trained the reasoning lane into the weights"
out.append(f'<g class="ms" data-tip="{shift_tip}">')
out.append(f'<line {anim("a-drop", at(SHIFT))} x1="{x(SHIFT)}" y1="22" x2="{x(SHIFT)}" y2="{bottom}" stroke="{AMBER}" '
           f'stroke-width="2" stroke-dasharray="5 5"/>')
out.append(f'<text {anim("a-fade", at(SHIFT))} x="{x(SHIFT)}" y="15" text-anchor="middle" font-size="16" font-weight="600" '
           f'fill="{AMBER}">o1, R1 (2024-25): reasoning trained into the model</text>')
out.append("</g>")
out.append(f'<line {anim("a-grow", at(Y0), round((Y1 - Y0) * SPEED, 2))} x1="{X0}" y1="{bottom + 12}" x2="{X1}" '
           f'y2="{bottom + 12}" stroke="{INK3}" stroke-width="1.5"/>')
for year in range(2020, 2027):
    out.append(f'<line {anim("a-fade", at(year))} x1="{x(year)}" y1="{bottom + 6}" x2="{x(year)}" y2="{bottom + 18}" '
               f'stroke="{INK3}" stroke-width="1.5"/>')
    out.append(f'<text {anim("a-fade", at(year))} x="{x(year)}" y="{bottom + 38}" text-anchor="middle" '
               f'font-family="var(--mono)" font-size="16" fill="{INK3}">{year}</text>')
out.append('</svg>')
svg = "\n    ".join(out)

html = DECK.read_text(encoding="utf-8")
html, n = re.subn(r'<svg class="tl".*?</svg>', lambda m: svg, html, count=1, flags=re.S)
assert n == 1, "no <svg class=\"tl\"> in deck"
DECK.write_text(html, encoding="utf-8")
print("timeline svg replaced")
