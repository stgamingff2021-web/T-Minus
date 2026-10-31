"""One-page officer briefing (spec §6.9): server-side HTML -> PDF (WeasyPrint).

Sections: situation; evidence with numbers, sources and timestamps; actions by phase
with citations; open questions; data warnings; draft public messages. Everything is
rendered from the stored plan and assessment — no new numbers are produced here.
"""
from __future__ import annotations

import datetime as dt
import html
from typing import Any

from app.agent.schemas import PHASES

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _t(iso_or_dt: Any) -> str:
    if not iso_or_dt:
        return "—"
    t = iso_or_dt if isinstance(iso_or_dt, dt.datetime) else dt.datetime.fromisoformat(str(iso_or_dt))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(IST).strftime("%d %b %Y %H:%M IST")


def e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def render_html(*, district: str, state: str, plan: dict, assessment: dict | None, actions: list[dict],
                comms: dict[str, str] | None, run_meta: dict) -> str:
    level = plan.get("level", "QUIET")
    head = (assessment or {}).get("headline") or {}
    color = {"ACT": "#c62828", "PREPARE": "#ef6c00", "WATCH": "#f9a825"}.get(level, "#2e7d32")
    rows = []
    for ph in PHASES:
        items = [a for a in actions if a.get("phase") == ph and a.get("status") != "rejected"]
        if not items:
            continue
        lis = []
        for a in items:
            text = a.get("edited_text") or a.get("action")
            cites = "; ".join(f"{e(c.get('document_title'))}, p. {e(c.get('page'))}" for c in a.get("citations") or [])
            src = {"district_plan": "Plan", "generic_guidance": "NDMA generic", "suggested": "Suggested — not in district plan"}.get(a.get("source"), a.get("source"))
            status = a.get("status", "proposed")
            lis.append(
                f"<li><b>{e(a.get('key') or a.get('id'))}</b> {e(text)}"
                f"<div class='meta'>{e(a.get('responsible') or 'Responsibility not stated in plan')} · {e(src)}"
                f"{' · ' + cites if cites else ''} · <i>{e(status)}</i></div></li>"
            )
        rows.append(f"<h3>{e(ph)}</h3><ul>{''.join(lis)}</ul>")
    probs_line = ""
    if head.get("probability_pct") is not None:
        probs_line = (f"{e(head.get('probability_pct'))}% ({e(head.get('members_exceeding'))} of {e(head.get('valid_members'))} forecasts) "
                      f"of a 24-hour total ≥ {e(head.get('threshold_mm'))} mm within {e(head.get('within_h'))} h")
    warn = "".join(f"<li>{e(w)}</li>" for w in plan.get("warnings") or []) or "<li>None</li>"
    oq = "".join(f"<li>{e(q)}</li>" for q in plan.get("open_questions") or []) or "<li>None</li>"
    msgs = ""
    if comms:
        msgs = (f"<h2>Draft public messages (require approval)</h2>"
                f"<p><b>EN:</b> {e(comms.get('public_en'))}</p><p class='ta'><b>TA:</b> {e(comms.get('public_ta'))}</p>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>T-Minus briefing — {e(district)}</title>
<style>
@page {{ size: A4; margin: 12mm 12mm 14mm 12mm; @bottom-center {{ content: "T-Minus prototype · decision support, not an official warning · page " counter(page); font-size: 7pt; color: #666; }} }}
body {{ font-family: "Noto Sans", "Noto Sans Tamil", "DejaVu Sans", sans-serif; font-size: 8.6pt; color: #111; }}
h1 {{ font-size: 14pt; margin: 0 0 2mm; }} h2 {{ font-size: 10pt; margin: 3mm 0 1mm; border-bottom: 1px solid #ccc; }}
h3 {{ font-size: 9pt; margin: 2mm 0 1mm; color: #333; }} ul {{ margin: 0 0 1mm 4mm; padding: 0; }} li {{ margin-bottom: 1mm; }}
.badge {{ display: inline-block; padding: 1mm 3mm; color: #fff; background: {color}; font-weight: bold; border-radius: 2mm; }}
.meta {{ color: #555; font-size: 7.4pt; }} .grid {{ display: flex; gap: 6mm; }} .grid > div {{ flex: 1; }}
.ta {{ font-family: "Noto Sans Tamil", sans-serif; }} .small {{ font-size: 7.4pt; color: #444; }}
</style></head><body>
<h1>T-Minus briefing · {e(district)}, {e(state)} <span class="badge">{e(level)}</span></h1>
<div class="small">Hazard: {e(plan.get('hazard'))} · Generated {_t(plan.get('generated_at'))} · Run {e(run_meta.get('run_id'))} · Model {e(run_meta.get('model'))} · Status {e(run_meta.get('status'))}</div>
<h2>Situation</h2><p>{e(plan.get('evidence_summary'))}</p>
<h2>Evidence</h2><ul>
<li>Probability: {probs_line or '—'}</li>
<li>Expected impact: T-minus {e(head.get('t_minus_h'))} h · earliest plausible onset in {e(head.get('earliest_onset_h'))} h</li>
<li>Source: {e((assessment or {}).get('model'))} ensemble, model run {e((assessment or {}).get('model_run'))}, data fetched {_t((assessment or {}).get('data_time'))}</li>
<li>Official alerts: {e(', '.join(f"{a.get('agency')} {a.get('color') or a.get('severity')}" for a in (assessment or {}).get('official_alerts') or []) or 'none active')}</li>
<li>Antecedent rain (72 h): {e((assessment or {}).get('antecedent_rain_72h_mm'))} mm · escalations: {e(', '.join((assessment or {}).get('escalations') or []) or 'none')}</li>
</ul>
<h2>Actions by phase (anchored to expected impact)</h2>{''.join(rows) or '<p>No actions.</p>'}
<div class="grid"><div><h2>Open questions</h2><ul>{oq}</ul></div><div><h2>Data warnings</h2><ul>{warn}</ul></div></div>
{msgs}
<p class="small">Every number above comes from the T-Minus assessment (traceable to the stored raw API response) or from the cited plan page. Nothing is sent without an officer's approval.</p>
</body></html>"""


def render_pdf(html_text: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html_text).write_pdf()
