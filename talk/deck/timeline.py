"""Regenerate the harness-lineage timeline SVG on main slide 4 of the deck.

    python timeline.py index.html   # edit LANES, then rerun; swaps the <svg class="tl"> block in place
"""
import re
import sys
from pathlib import Path

DECK = Path(sys.argv[1])
BLUE, AMBER, RED, INK2, INK3, SURF = "#2b6cd4", "#a8690c", "#c72d26", "#45464a", "#8a8b90", "#f7f7f8"
W, H = 1376, 460
X0, X1, Y0, Y1 = 170, 1180, 2020.0, 2026.8
SHIFT = 2024.7  # o1 (Sep 2024) to R1 (Jan 2025)


def x(year):
    return round(X0 + (year - Y0) / (Y1 - Y0) * (X1 - X0), 1)


# name, subtitle, color, fate, bar start, dashed, [(year, label, above)]
LANES = [
    ("KNOW", "context", BLUE, "in the harness", 2020.2, False,
     [(2020.4, "prompt, RAG", True), (2023.8, "MemGPT", True), (2025.4, "memory files, compaction", True)]),
    ("THINK", "reasoning", AMBER, "in the model", 2022.0, False,
     [(2022.1, "chain of thought, voting", True), (2023.2, "Reflexion", False), (2023.7, "Tree of Thoughts, LATS", True)]),
    ("ACT", "tools", BLUE, "in the harness", 2022.7, False,
     [(2022.8, "ReAct loop", True), (2023.45, "function calling", False), (2024.3, "SWE-agent, CodeAct", True),
      (2024.9, "MCP", False), (2025.6, "Claude Code, Codex, Antigravity", True)]),
    ("CONTROL", "permission", BLUE, "in the harness", 2023.3, False,
     [(2023.3, "AutoGPT asks y/n", True), (2025.4, "permission modes, sandboxes, hooks", True)]),
    ("PROVE", "verify, record", RED, "barely built", 2023.8, True,
     [(2023.8, "SWE-bench: tests as judge", True), (2026.5, "GATS, LEDGER: sensors, replayable journal", False)]),
]
LANE_H, TOP = 76, 27
LABEL = f'paint-order="stroke" stroke="#fff" stroke-width="5" stroke-linejoin="round"'

out = [f'<svg class="tl" viewBox="0 0 {W} {H}" style="width:100%;font-family:var(--sans)">']
bottom = TOP + LANE_H * len(LANES)
for i in range(1, len(LANES), 2):
    out.append(f'<rect x="0" y="{TOP + LANE_H * i}" width="{W}" height="{LANE_H}" fill="{SURF}"/>')
out.append(f'<line x1="{x(SHIFT)}" y1="22" x2="{x(SHIFT)}" y2="{bottom}" stroke="{AMBER}" stroke-width="2" '
           f'stroke-dasharray="5 5"/>')
for i, (name, sub, color, fate, start, dashed, dots) in enumerate(LANES):
    cy = TOP + LANE_H * i + LANE_H // 2
    out.append(f'<text x="0" y="{cy - 2}" font-family="var(--mono)" font-weight="600" font-size="17" '
               f'letter-spacing="2" fill="{color}">{name}</text>')
    out.append(f'<text x="0" y="{cy + 19}" font-size="15" fill="{INK3}">{sub}</text>')
    end = x(SHIFT) if name == "THINK" else X1
    if dashed:
        out.append(f'<line x1="{x(start)}" y1="{cy}" x2="{end}" y2="{cy}" stroke="{color}" stroke-width="6" '
                   f'stroke-dasharray="14 10" stroke-linecap="round"/>')
    else:
        out.append(f'<rect x="{x(start)}" y="{cy - 5}" width="{round(end - x(start), 1)}" height="10" rx="5" fill="{color}"/>')
    if name == "THINK":
        out.append(f'<rect x="{x(SHIFT)}" y="{cy - 5}" width="{round(X1 - x(SHIFT), 1)}" height="10" rx="5" fill="{AMBER}" opacity=".28"/>')
        out.append(f'<text x="{round((x(SHIFT) + X1) / 2)}" y="{cy + 28}" text-anchor="middle" font-size="16" '
                   f'font-style="italic" fill="{AMBER}">inside the model now</text>')
    for year, label, above in dots:
        cx = x(year)
        out.append(f'<circle cx="{cx}" cy="{cy}" r="8" fill="#fff" stroke="{color}" stroke-width="4"/>')
        if year == 2026.5:
            out.append(f'<text x="{X1}" y="{cy + 28}" text-anchor="end" font-size="16" font-weight="600" '
                       f'fill="{color}" {LABEL}>{label}</text>')
        else:
            # keep labels off the dashed 2024 marker: shift any label that would straddle it to one side
            half, m = len(label) * 4.2, x(SHIFT)
            tx, anchor = cx, "middle"
            if cx <= m < cx + half + 6:
                tx, anchor = m - 10, "end"
            elif cx - half - 6 < m < cx:
                tx, anchor = m + 10, "start"
            out.append(f'<text x="{tx}" y="{cy - 16 if above else cy + 28}" text-anchor="{anchor}" font-size="16" '
                       f'fill="{INK2}" {LABEL}>{label}</text>')
    out.append(f'<text x="1206" y="{cy + 6}" font-size="17" font-weight="700" fill="{color}">{fate}</text>')

out.append(f'<text x="{x(SHIFT)}" y="15" text-anchor="middle" font-size="16" font-weight="600" fill="{AMBER}">'
           f'o1, R1 (2024-25): reasoning trained into the model</text>')
out.append(f'<line x1="{X0}" y1="{bottom + 12}" x2="{X1}" y2="{bottom + 12}" stroke="{INK3}" stroke-width="1.5"/>')
for year in range(2020, 2027):
    out.append(f'<line x1="{x(year)}" y1="{bottom + 6}" x2="{x(year)}" y2="{bottom + 18}" stroke="{INK3}" stroke-width="1.5"/>')
    out.append(f'<text x="{x(year)}" y="{bottom + 38}" text-anchor="middle" font-family="var(--mono)" font-size="16" '
               f'fill="{INK3}">{year}</text>')
out.append('</svg>')
svg = "\n    ".join(out)

html = DECK.read_text(encoding="utf-8")
html, n = re.subn(r'<svg class="tl".*?</svg>', lambda m: svg, html, count=1, flags=re.S)
assert n == 1, "no <svg class=\"tl\"> in deck"
DECK.write_text(html, encoding="utf-8")
print("timeline svg replaced")
