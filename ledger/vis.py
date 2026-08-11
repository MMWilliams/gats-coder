"""`ledger vis`: render a journal into one self-contained HTML report.

Everything the journal proves -- the plan, every UCB decision table, every
gate, hook, execution, observation, incident, and the chain verdict -- laid
out as a readable timeline. No external assets, no JavaScript beyond what
<details> gives us for free; the file works from disk, in mail, in CI
artifacts, forever.
"""

from __future__ import annotations

import html
from pathlib import Path

from .journal import Journal

_CSS = """
:root {
  --bg: #0e1014; --panel: #161920; --panel2: #1c202a; --line: #262b37;
  --ink: #e8eaf0; --dim: #8b90a0; --mono: ui-monospace, 'Cascadia Code',
  Menlo, Consolas, monospace;
  --good: #3fd68f; --bad: #ff6b6b; --warn: #ffc857; --accent: #56c8ff;
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink); font: 15px/1.6 system-ui,
  -apple-system, 'Segoe UI', sans-serif; padding: 40px 18px 80px; }
.wrap { max-width: 880px; margin: 0 auto; }
.brand { font-size: 12px; letter-spacing: .28em; color: var(--accent);
  font-weight: 700; }
h1 { font-size: 26px; line-height: 1.25; margin: 10px 0 16px;
  letter-spacing: -.01em; }
.badges { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 26px; }
.badge { font: 600 12px/1 var(--mono); padding: 6px 11px; border-radius: 99px;
  border: 1px solid var(--line); color: var(--dim); }
.badge.good { color: var(--good); border-color: color-mix(in srgb,
  var(--good) 40%, transparent); }
.badge.bad { color: var(--bad); border-color: color-mix(in srgb,
  var(--bad) 40%, transparent); }
.badge.warn { color: var(--warn); border-color: color-mix(in srgb,
  var(--warn) 40%, transparent); }
.stats { display: grid; grid-template-columns: repeat(auto-fit,
  minmax(120px, 1fr)); gap: 10px; margin-bottom: 34px; }
.stat { background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; padding: 12px 14px; }
.stat b { display: block; font-size: 20px; font-weight: 650; }
.stat span { font-size: 11.5px; color: var(--dim); text-transform: uppercase;
  letter-spacing: .08em; }
.step { background: var(--panel); border: 1px solid var(--line);
  border-radius: 14px; padding: 16px 18px; margin-bottom: 14px;
  position: relative; }
.step-head { display: flex; align-items: baseline; gap: 12px;
  flex-wrap: wrap; }
.step-n { font: 700 12px/1 var(--mono); color: var(--dim); }
.action { font: 650 15px var(--mono); color: var(--accent); }
.action .slot { color: var(--dim); font-weight: 400; }
.pred { font-size: 12.5px; color: var(--dim); }
.rowline { display: flex; gap: 10px; align-items: baseline; margin-top: 9px;
  font-size: 13.5px; flex-wrap: wrap; }
.tag { font: 650 10.5px/1 var(--mono); letter-spacing: .1em; padding: 4px 8px;
  border-radius: 6px; background: var(--panel2); color: var(--dim);
  flex-shrink: 0; }
.tag.good { color: var(--good); } .tag.bad { color: var(--bad); }
.tag.warn { color: var(--warn); }
.mono { font-family: var(--mono); font-size: 12.5px; }
.dim { color: var(--dim); }
details { margin-top: 10px; }
summary { cursor: pointer; font-size: 12.5px; color: var(--dim);
  user-select: none; }
summary:hover { color: var(--ink); }
table { border-collapse: collapse; margin-top: 8px; width: 100%;
  font: 12.5px var(--mono); }
th { text-align: left; color: var(--dim); font-weight: 600; padding: 5px 10px;
  border-bottom: 1px solid var(--line); }
td { padding: 5px 10px; border-bottom: 1px solid
  color-mix(in srgb, var(--line) 50%, transparent); }
tr.chosen td { color: var(--accent); font-weight: 650; }
.props { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.prop { font: 11.5px var(--mono); padding: 3px 8px; border-radius: 6px;
  background: var(--panel2); color: var(--dim); }
.prop.add { color: var(--good); }
.prop.del { color: var(--bad); text-decoration: line-through; }
.incident { border-color: color-mix(in srgb, var(--bad) 45%, transparent); }
.banner { border: 1px solid color-mix(in srgb, var(--bad) 45%, transparent);
  background: color-mix(in srgb, var(--bad) 8%, var(--panel));
  border-radius: 14px; padding: 16px 18px; margin: 20px 0; }
.banner b { color: var(--bad); }
.divider { text-align: center; color: var(--dim); font-size: 12px;
  margin: 26px 0 12px; letter-spacing: .12em; text-transform: uppercase; }
footer { margin-top: 44px; color: var(--dim); font-size: 12px;
  border-top: 1px solid var(--line); padding-top: 16px; }
"""


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _action_html(name: str) -> str:
    family, _, slot = str(name).partition(":")
    slot_html = f'<span class="slot">:{_e(slot)}</span>' if slot else ""
    return f'<span class="action">{_e(family)}{slot_html}</span>'


def _badge(text: str, kind: str = "") -> str:
    return f'<span class="badge {kind}">{_e(text)}</span>'


def _tag(text: str, kind: str = "") -> str:
    return f'<span class="tag {kind}">{_e(text)}</span>'


def _ucb_table(plan: dict) -> str:
    rows = []
    for row in sorted(plan.get("table", []), key=lambda r: -r.get("q", 0)):
        cls = ' class="chosen"' if row.get("a") == plan.get("chosen") else ""
        p = row.get("p")
        rows.append(
            f"<tr{cls}><td>{_e(row.get('a'))}</td>"
            f"<td>{_e(row.get('n', 0))}</td>"
            f"<td>{row.get('q', 0.0):.3f}</td>"
            f"<td>{'—' if p is None else f'{p:g}'}</td>"
            f"<td>{_e(row.get('layer') or '—')}</td></tr>")
    return ("<details><summary>decision table "
            f"({len(rows)} arms)</summary><table>"
            "<tr><th>arm</th><th>N</th><th>Q</th><th>p</th><th>layer</th></tr>"
            + "".join(rows) + "</table></details>")


def _props_diff(props: list, prev: list | None) -> str:
    cur, old = set(props), set(prev or [])
    chips = []
    for p in sorted(cur - old):
        chips.append(f'<span class="prop add">+ {_e(p)}</span>')
    for p in sorted(old - cur):
        chips.append(f'<span class="prop del">{_e(p)}</span>')
    if not chips:
        chips.append('<span class="prop">no state change</span>')
    body = f'<div class="props">{"".join(chips)}</div>'
    full = "".join(f'<span class="prop">{_e(p)}</span>' for p in sorted(cur))
    return (body + f"<details><summary>full state ({len(cur)} propositions)"
                   f'</summary><div class="props">{full}</div></details>')


def _exec_row(e: dict) -> str:
    ok = e.get("ok")
    tag = _tag("EXEC OK", "good") if ok else _tag("EXEC FAILED", "bad")
    claims = "; ".join(str(c) for c in e.get("claims", [])) or "—"
    cmds = "".join(
        f'<div class="mono dim">$ {_e(c.get("cmd"))} '
        f'<span class="dim">(exit {_e(c.get("exit"))})</span></div>'
        for c in e.get("commands", []))
    detail = (f"<details><summary>commands "
              f"({len(e.get('commands', []))})</summary>{cmds}</details>"
              if cmds else "")
    return (f'<div class="rowline">{tag}'
            f'<span class="dim">{_e(claims)}</span></div>{detail}')


def render(entries: list[dict], chain_ok: bool, chain_err: str | None,
           source: str) -> str:
    header = entries[0] if entries and entries[0].get("t") == "HEADER" else {}
    end = next((e for e in reversed(entries) if e.get("t") == "END"), {})
    esc = next((e for e in reversed(entries)
                if e.get("t") == "ESCALATION"), None)
    incidents = [e for e in entries if e.get("t") == "INCIDENT"]
    divergences = sum(1 for e in entries
                      if e.get("t") == "OBSERVE" and e.get("diverged"))
    denials = sum(1 for e in entries
                  if e.get("t") == "GATE" and not e.get("approved", True))
    task = header.get("task_text") or header.get("task_id") or source

    badges = []
    if end:
        badges.append(_badge("SUCCESS", "good") if end.get("success")
                      else _badge("GOAL NOT REACHED", "bad"))
    if esc is not None:
        badges.append(_badge(f"escalated: {esc.get('reason', '?')}", "bad"))
    badges.append(_badge("chain intact", "good") if chain_ok
                  else _badge(f"chain BROKEN: {chain_err}", "bad"))
    if header.get("profile"):
        badges.append(_badge(f"profile {header['profile']}"))
    if header.get("resumed"):
        badges.append(_badge(f"resumed · segment {header.get('segment')}",
                             "warn"))
    for g in header.get("goal", []):
        badges.append(_badge(f"goal: {g}"))

    stats = [
        (str(end.get("steps", 0)), "steps"),
        (str(end.get("llm_planning_calls", 0)), "llm planning calls"),
        (str(len(incidents)), "incidents"),
        (str(divergences), "divergences"),
        (str(denials), "denials"),
        (str(len(entries)), "journal entries"),
    ]
    stats_html = "".join(f'<div class="stat"><b>{_e(v)}</b>'
                         f"<span>{_e(label)}</span></div>"
                         for v, label in stats)

    # Timeline: group everything between one PLAN and the next.
    cards = []
    current: dict | None = None
    prev_obs: list | None = None

    def close(cur):
        if cur is not None:
            cards.append(cur)

    for e in entries:
        t = e.get("t")
        if t == "PLAN":
            close(current)
            current = {"plan": e, "events": []}
        elif t in ("GATE", "HOOK", "EXEC", "INCIDENT", "OBSERVE") \
                and current is not None:
            current["events"].append(e)
    close(current)

    steps_html = []
    for card in cards:
        plan = card["plan"]
        wm = plan.get("wm", {})
        body = [
            '<div class="step-head">',
            f'<span class="step-n">STEP {_e(plan.get("step"))}</span>',
            _action_html(plan.get("chosen", "?")),
            f'<span class="pred">predicted via {_e(wm.get("layer", "?"))} '
            f'(p={_e(wm.get("p", "?"))}) · state '
            f'{_e(str(plan.get("state", ""))[:12])}</span>',
            "</div>",
            _ucb_table(plan),
        ]
        incident_here = False
        for e in card["events"]:
            t = e.get("t")
            if t == "GATE":
                if e.get("approved"):
                    tag = _tag("GATE APPROVED", "good")
                else:
                    tag = _tag("GATE DENIED", "bad")
                reason = e.get("reason")
                body.append(
                    f'<div class="rowline">{tag}<span class="dim">'
                    f'risk {_e(e.get("risk"))}'
                    + (f" — {_e(reason)}" if reason else "")
                    + "</span></div>")
            elif t == "HOOK":
                kind = "bad" if e.get("blocked") else "good"
                label = "HOOK BLOCKED" if e.get("blocked") else \
                    f"HOOK {str(e.get('event', '')).upper()}"
                body.append(
                    f'<div class="rowline">{_tag(label, kind)}'
                    f'<span class="mono dim">$ {_e(e.get("cmd"))} '
                    f'(exit {_e(e.get("exit"))})</span></div>')
            elif t == "EXEC":
                body.append(_exec_row(e))
            elif t == "INCIDENT":
                incident_here = True
                claimed = ", ".join(e.get("claimed_not_observed", []))
                body.append(
                    f'<div class="rowline">{_tag("INCIDENT", "bad")}'
                    f'<span class="dim">the model claimed what the sensors '
                    f"refused to confirm: </span>"
                    f'<span class="mono">{_e(claimed)}</span></div>')
            elif t == "OBSERVE":
                if e.get("diverged"):
                    body.append(
                        f'<div class="rowline">{_tag("DIVERGED", "warn")}'
                        '<span class="dim">reality disagreed with the '
                        "prediction — the model layer was retrained on the "
                        "spot</span></div>")
                body.append(_props_diff(e.get("obs_props", []), prev_obs))
                prev_obs = e.get("obs_props", [])
        cls = "step incident" if incident_here else "step"
        steps_html.append(f'<div class="{cls}">{"".join(body)}</div>')

    esc_html = ""
    if esc is not None:
        esc_html = (
            '<div class="banner"><b>Escalated to the human</b> — reason: '
            f'<span class="mono">{_e(esc.get("reason"))}</span>'
            f'<span class="dim"> (denials {_e(esc.get("denials", 0))}, '
            f'author attempts {_e(esc.get("author_attempts", 0))}'
            f'/{_e(esc.get("author_attempt_limit", 0))})</span></div>')

    version = header.get("version", "?")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LEDGER — {_e(task)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<div class="brand">LEDGER · EPISODE REPORT</div>
<h1>{_e(task)}</h1>
<div class="badges">{"".join(badges)}</div>
<div class="stats">{stats_html}</div>
<div class="divider">timeline</div>
{"".join(steps_html) or '<p class="dim">no plan steps recorded</p>'}
{esc_html}
<footer>agent v{_e(version)} · source {_e(source)} · every entry above is
hash-chained; run <span class="mono">ledger verify-chain</span> and
<span class="mono">ledger replay</span> to check this file's claims against
the journal itself.</footer>
</div></body></html>"""


def render_file(journal_path: str | Path) -> str:
    entries = Journal.read(str(journal_path))
    chain_ok, chain_err = Journal.verify_chain(str(journal_path))
    return render(entries, chain_ok, chain_err, Path(journal_path).name)
