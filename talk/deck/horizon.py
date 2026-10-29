"""Regenerate the long-horizon chart (METR time horizons + projection + responsibilities band) in the deck.

    python horizon.py index.html   # swaps the <svg class="hz"> block in place

Data: data/metr_horizon_v1_1.yaml, downloaded 2026-10-26 from https://metr.org/assets/benchmark_results_1_1.yaml
(METR Time Horizon 1.1: human time-to-complete of tasks an agent finishes 50% of the time).
"""
import math
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
DECK = HERE / sys.argv[1]
INK, INK2, INK3, LINE = "#141518", "#45464a", "#8a8b90", "#e5e5e7"
BLUE, AMBER, RED, GREEN = "#2b6cd4", "#a8690c", "#c72d26", "#0f7a5a"
W, H = 1376, 490
X0, X1, YR0, YR1 = 130, 1330, 2019.0, 2028.5
Y_BOT, Y_TOP, LO, HI = 430, 112, math.log10(0.03), math.log10(10020)  # minutes; top = 1 work month (167 h)
RELIABLE_MAX = 16 * 60  # METR: estimates past 16 h are unreliable with the current suite

# label, unlock (what a human-time task of that length looks like), label dx, dy, anchor
LABELS = {
    "gpt2": ("GPT-2", "answer a lookup", 12, -14, "start"),
    "davinci_002": ("GPT-3", "", 10, -14, "start"),
    "gpt_3_5_turbo_instruct": ("GPT-3.5", "write a function", -12, -26, "end"),
    "gpt_4": ("GPT-4", "fix a small bug", -12, -26, "end"),
    "o1_inspect": ("o1", "debug across files", -14, -24, "end"),
    "gpt_5_2025_08_07_inspect": ("GPT-5", "build a feature", -14, -30, "end"),
    "claude_opus_4_6_inspect": ("Claude Opus 4.6, Mythos", "a day's project", 16, 34, "start"),
}
GRID = [(0.05, "seconds"), (1, "1 min"), (10, "10 min"), (60, "1 hour"), (480, "1 workday"),
        (2400, "1 work week"), (10020, "1 work month")]
ERAS = [(2019.0, 2022.7, "prompts", "#eeeef0", INK2, False), (2022.7, 2024.7, "tool loops", "#eaf1fc", BLUE, False),
        (2024.7, 2026.8, "reasoning + harnesses", "#f8f0e2", AMBER, False),
        (2026.8, 2027.85, "planning + checks", "#e8f4ef", GREEN, True), (2027.85, 2028.5, "always-on", "#fbeceb", RED, True)]


def yearf(d):
    return d.year + (d.timetuple().tm_yday - 1) / 365.25


def fmt_year(y):
    d = date(int(y), 1, 1).toordinal() + int((y % 1) * 365.25)
    return date.fromordinal(d).strftime("%b %Y")


def x(y):
    return round(X0 + (y - YR0) / (YR1 - YR0) * (X1 - X0), 1)


def ym(minutes):
    return round(Y_BOT - (math.log10(minutes) - LO) / (HI - LO) * (Y_BOT - Y_TOP), 1)


raw = (HERE / "data" / "metr_horizon_v1_1.yaml").read_text(encoding="utf-8")
ci_hi, ci_lo, dbl = map(float, re.search(
    r"from_2023_on:\n\s+ci_high: ([\d.]+)\n\s+ci_low: ([\d.]+)\n\s+point_estimate: ([\d.]+)", raw).groups())
pts = []
for key, body in re.findall(r"\n  (\w[\w\-.]*):\n    benchmark_name(.*?)(?=\n  \w|\Z)", raw, re.S):
    if "is_sota: true" in body:
        est = float(re.search(r"p50_horizon_length:\n\s+ci_high: [\d.]+\n\s+ci_low: [\d.]+\n\s+estimate: ([\d.]+)",
                              body).group(1))
        pts.append((yearf(date.fromisoformat(re.search(r"release_date: (\S+)", body).group(1))), key, est))
pts.sort()

# METR's post-2023 doubling time as the slope; intercept by least squares over the reliable post-2023 points
slope = 365.25 / dbl
fit = [(t, math.log2(m)) for t, _, m in pts if t >= 2023 and m <= RELIABLE_MAX]
icpt = sum(lm - slope * t for t, lm in fit) / len(fit)
trend = lambda t: 2 ** (slope * t + icpt)
T_ANCHOR = max(t for t, _, _ in pts)
M_ANCHOR = 2 ** (slope * T_ANCHOR + icpt)
cross = lambda m, s: T_ANCHOR + (math.log2(m) - math.log2(M_ANCHOR)) / s
week, month = cross(2400, slope), cross(10020, slope)
month_fast, month_slow = cross(10020, 365.25 / ci_lo), cross(10020, 365.25 / ci_hi)

o = [f'<svg class="hz" viewBox="0 0 {W} {H}" style="width:100%;font-family:var(--sans)">']
# responsibilities band: a change of kind, drawn above the axis rather than on it
o.append('<g class="frag">')
o.append(f'<rect x="{X0}" y="6" width="{X1 - X0}" height="86" rx="12" fill="#fbeceb" stroke="{RED}" '
         f'stroke-dasharray="6 6" stroke-opacity=".55"/>')
o.append(f'<text x="{X0 + 20}" y="34" font-size="19" font-weight="800" fill="{RED}">∞  Responsibilities: no completion state</text>')
o.append(f'<text x="{X0 + 20}" y="60" font-size="16" fill="{INK2}">keep prod healthy · run on-call · '
         f'manage a team · maintain a codebase</text>')
o.append(f'<text x="{X0 + 20}" y="82" font-size="15" fill="{INK3}">Not a cron job or a pipeline (triggered, stateless): '
         f'one agent that owns its state, runs continuously, and is judged on invariants held, not goals reached.</text>')
o.append(f'<text x="{X0 - 12}" y="54" text-anchor="end" font-size="15" font-weight="700" fill="{RED}">no end</text>')
o.append('</g>')
for m, lab in GRID:
    o.append(f'<line x1="{X0}" y1="{ym(m)}" x2="{X1}" y2="{ym(m)}" stroke="{LINE}" stroke-width="1"/>')
    o.append(f'<text x="{X0 - 12}" y="{ym(m) + 5}" text-anchor="end" font-size="15" fill="{INK3}">{lab}</text>')
# projection cone (95% CI on the doubling time) from the last measured date to the month line
cone_top = [(t, min(10020, 2 ** ((t - T_ANCHOR) * 365.25 / ci_lo) * M_ANCHOR)) for t in (T_ANCHOR, month_fast)]
cone_bot = [(t, 2 ** ((t - T_ANCHOR) * 365.25 / ci_hi) * M_ANCHOR) for t in (month_slow, T_ANCHOR)]
o.append('<polygon points="' + " ".join(f"{x(t)},{ym(m)}" for t, m in cone_top + [(month_slow, 10020)] + cone_bot)
         + f'" fill="{BLUE}" opacity=".08"/>')
t_lo = 2023.0
o.append(f'<line x1="{x(t_lo)}" y1="{ym(trend(t_lo))}" x2="{x(T_ANCHOR)}" y2="{ym(M_ANCHOR)}" stroke="{BLUE}" '
         f'stroke-width="3" stroke-opacity=".5"/>')
o.append(f'<line x1="{x(T_ANCHOR)}" y1="{ym(M_ANCHOR)}" x2="{x(month)}" y2="{ym(10020)}" stroke="{BLUE}" '
         f'stroke-width="3" stroke-dasharray="8 7"/>')
for t, key, m in pts:
    hollow = m > RELIABLE_MAX
    o.append(f'<circle cx="{x(t)}" cy="{ym(m)}" r="7" fill="{"#fff" if hollow else BLUE}" stroke="{BLUE}" stroke-width="3"/>')
    if key in LABELS:
        name, unlock, dx, dy, anchor = LABELS[key]
        lx, ly = x(t) + dx, ym(m) + dy
        o.append(f'<text x="{lx}" y="{ly}" text-anchor="{anchor}" font-size="16" font-weight="700" fill="{INK}" '
                 f'paint-order="stroke" stroke="#fff" stroke-width="5">{name}</text>')
        if unlock:
            o.append(f'<text x="{lx}" y="{ly + 18}" text-anchor="{anchor}" font-size="15" fill="{INK2}" '
                     f'paint-order="stroke" stroke="#fff" stroke-width="5">{unlock}</text>')
for t, m, name, when, side in [(week, 2400, "a week's project", fmt_year(week), 1),
                               (month, 10020, "a month's project",
                                f"{fmt_year(month)} (CI {fmt_year(month_fast)} – {fmt_year(month_slow)})", -1)]:
    lx, anchor = x(t) + 14 * side, "start" if side > 0 else "end"
    o.append(f'<circle cx="{x(t)}" cy="{ym(m)}" r="8" fill="#fff" stroke="{BLUE}" stroke-width="3" stroke-dasharray="4 3"/>')
    o.append(f'<text x="{lx}" y="{ym(m) + 22}" text-anchor="{anchor}" font-size="16" font-weight="700" fill="{BLUE}" '
             f'paint-order="stroke" stroke="#fff" stroke-width="5">{name}</text>')
    o.append(f'<text x="{lx}" y="{ym(m) + 40}" text-anchor="{anchor}" font-size="15" fill="{INK3}" '
             f'paint-order="stroke" stroke="#fff" stroke-width="5">trend: {when}</text>')
o.append(f'<line x1="{x(month)}" y1="{ym(10020) - 10}" x2="{x(month)}" y2="96" stroke="{RED}" stroke-width="2.5" '
         f'stroke-dasharray="4 4"/>')
o.append(f'<text x="{x(month) + 10}" y="{ym(10020) - 6}" font-size="20" font-weight="800" fill="{RED}">?</text>')
for a, b, lab, fill, ink, projected in ERAS:
    dash = f' stroke="{ink}" stroke-dasharray="5 4" stroke-opacity=".6"' if projected else ""
    o.append(f'<rect x="{x(a) + 2}" y="{Y_BOT + 10}" width="{x(b) - x(a) - 4}" height="24" rx="6" fill="{fill}"{dash}/>')
    o.append(f'<text x="{(x(a) + x(b)) / 2}" y="{Y_BOT + 27}" text-anchor="middle" font-size="14" font-weight="700" '
             f'fill="{ink}">{lab}</text>')
for yv in range(2019, 2029):
    o.append(f'<text x="{x(yv)}" y="{H - 8}" text-anchor="middle" font-family="var(--mono)" font-size="15" '
             f'fill="{INK3}">{yv}</text>')
o.append("</svg>")
svg = "\n    ".join(o)

html = DECK.read_text(encoding="utf-8")
html, n = re.subn(r'<svg class="hz".*?</svg>', lambda _: svg, html, count=1, flags=re.S)
assert n == 1, 'no <svg class="hz"> in deck'
DECK.write_text(html, encoding="utf-8")
print(f"horizon svg replaced; week {fmt_year(week)}, month {fmt_year(month)} "
      f"(CI {fmt_year(month_fast)} - {fmt_year(month_slow)}), doubling {dbl:.0f} d")
