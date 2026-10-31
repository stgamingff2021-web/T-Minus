"""Agent run lifecycle: create -> stream events -> execute graph -> persist plan/comms.

Each event is persisted to ``agent_events`` (so SSE clients can resume from their
Last-Event-ID) and published live to ``run:<id>`` subscribers within milliseconds.
Results are stored as *proposed*: nothing leaves the system without an officer.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import threading
from typing import Any

from sqlalchemy import func, select

from app import notify
from app.agent.graph import RunCtx, build_graph, key_numbers
from app.agent.llm import get_llm
from app.agent.tools import ToolContext
from app.comms import sms
from app.comms.briefing import render_html
from app.comms.cap import Area, InfoBlock, build_cap, certainty, severity, urgency, validate_cap, EVENT_NAME, RESPONSE
from app.config import get_config, get_settings
from app.db.models import ActionItemRow, AgentEvent, AgentRun, Communication, District, Trigger, utcnow
from app.db.session import session_scope
from app.geo import cap_polygon

log = logging.getLogger("tminus.agent")
_tasks: dict[str, asyncio.Task] = {}
_seq_lock = threading.Lock()
_seq: dict[str, int] = {}
_graph = None


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def emitter(run_id: str):
    def emit(type_: str, payload: dict) -> None:
        with _seq_lock:
            if run_id not in _seq:
                with session_scope() as s:
                    _seq[run_id] = int(s.execute(select(func.max(AgentEvent.seq)).where(AgentEvent.run_id == run_id)).scalar() or 0)
            _seq[run_id] += 1
            seq = _seq[run_id]
        ts = utcnow()
        with session_scope() as s:
            s.add(AgentEvent(run_id=run_id, seq=seq, ts=ts, type=type_, payload=payload))
        notify.publish(f"run:{run_id}", {"seq": seq, "ts": ts.isoformat(), "type": type_, "payload": payload})
    return emit


def active_run_for(trigger_id: str | None, district_id: str, hazard: str) -> str | None:
    for rid, task in list(_tasks.items()):
        if task.done():
            _tasks.pop(rid, None)
    with session_scope() as s:
        q = select(AgentRun.id).where(AgentRun.status == "running", AgentRun.district_id == district_id,
                                      AgentRun.hazard == hazard)
        if trigger_id:
            q = q.where(AgentRun.trigger_id == trigger_id)
        rid = s.execute(q.order_by(AgentRun.started_at.desc()).limit(1)).scalar()
    return rid if rid in _tasks else None


def start_run_background(district_id: str, hazard: str, *, trigger_id: str | None = None,
                         started_by: str = "officer") -> str:
    existing = active_run_for(trigger_id, district_id, hazard)
    if existing:
        return existing
    llm = get_llm()
    run_id = create_run(district_id, hazard, trigger_id=trigger_id, started_by=started_by,
                        model=llm.name if llm else None)
    task = asyncio.get_running_loop().create_task(execute_run(run_id, llm))
    _tasks[run_id] = task
    return run_id


def create_run(district_id: str, hazard: str, *, trigger_id: str | None, started_by: str, model: str | None) -> str:
    with session_scope() as s:
        run = AgentRun(district_id=district_id, hazard=hazard, trigger_id=trigger_id, started_by=started_by,
                       model=model, status="running")
        s.add(run)
        s.flush()
        return run.id


async def execute_run(run_id: str, llm=None) -> None:
    cfg = get_config()
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        district_id, hazard, trigger_id = run.district_id, run.hazard, run.trigger_id
    d = cfg.district(district_id)
    emit = emitter(run_id)
    agent_cfg = cfg.hazards.get("agent", {})
    tools = ToolContext(run_id=run_id, district_id=district_id, hazard=hazard, emit=emit,
                        max_calls=int(agent_cfg.get("max_tool_calls", 20)))
    ctx = RunCtx(run_id=run_id, district_id=district_id, district_name=d.name if d else district_id, hazard=hazard,
                 tools=tools, llm=llm, emit=emit, soft_timeout_s=float(agent_cfg.get("soft_timeout_s", 120)),
                 max_attempts=int(agent_cfg.get("max_plan_attempts", 3)))
    emit("run", {"status": "started", "district": ctx.district_name, "hazard": hazard, "trigger_id": trigger_id,
                 "model": llm.name if llm else None, "budget": {"tool_calls": tools.max_calls,
                                                                "plan_attempts": ctx.max_attempts,
                                                                "soft_timeout_s": ctx.soft_timeout_s}})
    state: dict[str, Any] = {"run_id": run_id, "district_id": district_id, "district_name": ctx.district_name,
                             "hazard": hazard, "trigger_id": trigger_id, "status": "running"}
    try:
        final = await graph().ainvoke(state, config={"configurable": {"ctx": ctx}, "recursion_limit": 40})
    except Exception as exc:  # noqa: BLE001 - a crashed run is reported, never hidden
        log.exception("agent run %s crashed", run_id)
        emit("error", {"message": f"Run failed: {type(exc).__name__}: {exc}"[:400]})
        final = {**state, "status": "failed", "errors": [str(exc)]}
    try:
        persist(run_id, ctx, final)
    except Exception as exc:  # noqa: BLE001
        log.exception("persisting run %s failed", run_id)
        with session_scope() as s:
            r = s.get(AgentRun, run_id)
            r.status, r.error, r.finished_at = "failed", f"persist failed: {exc}", utcnow()
    with session_scope() as s:
        status = s.get(AgentRun, run_id).status
    emit("done", {"status": status, "tool_calls": tools.calls, "tokens_in": ctx.tokens_in, "tokens_out": ctx.tokens_out,
                  "elapsed_s": round(ctx.elapsed(), 1), "models": ctx.models_used})
    notify.publish("system", {"type": "run_finished", "run_id": run_id, "district_id": district_id, "status": status})


def _enrich_citations(cites: list[dict], tools: ToolContext) -> list[dict]:
    out = []
    for c in cites:
        ch = tools.retrieved.get(c.get("chunk_id")) or tools.generic.get(c.get("chunk_id"))
        if ch is None:
            out.append({**c, "document_id": None, "document_title": None, "page": None, "unverified": True})
            continue
        out.append({"chunk_id": ch["chunk_id"], "quote": c.get("quote", ""), "document_id": ch["document_id"],
                    "document_title": ch["document_title"], "page": ch["page"], "section": ch.get("section"),
                    "source_url": ch.get("source_url"), "kind": ch.get("kind"), "edition_year": ch.get("edition_year")})
    return out


def persist(run_id: str, ctx: RunCtx, final: dict) -> None:
    tools = ctx.tools
    status = final.get("status") or "failed"
    if status == "running":
        status = "completed"
    draft = final.get("draft_plan") or {}
    report = final.get("verification_report") or {}
    problems_by_action: dict[str, list[str]] = {}
    for p in report.get("problems") or []:
        problems_by_action.setdefault(p.get("action_id") or "_plan", []).append(p["message"])
    card = final.get("assessment")
    warnings = list(final.get("warnings") or []) + list(draft.get("warnings") or [])
    if tools.errors:
        warnings += [f"Tool error: {e}" for e in tools.errors[:6]]
    if status == "partial":
        warnings.insert(0, "PARTIAL PLAN: the verifier still found problems after all retries; problems are marked on each action.")
    if tools.retrieval_backend == "tfidf":
        warnings.append("Vector search fallback active (TF-IDF): semantic embeddings unavailable; keyword matching still applies.")
    plan = {
        "trigger_id": final.get("trigger_id"),
        "district_id": ctx.district_id,
        "hazard": ctx.hazard,
        "level": final.get("level") or "QUIET",
        "generated_at": utcnow().isoformat(),
        "evidence_summary": draft.get("evidence_summary") or final.get("evidence_summary_code"),
        "evidence_summary_code": final.get("evidence_summary_code"),
        "key_numbers": key_numbers(card),
        "open_questions": draft.get("open_questions") or [],
        "warnings": list(dict.fromkeys(warnings)),
        "plan_problems": problems_by_action.get("_plan", []),
        "gather_note": final.get("gather_note"),
        "critic": final.get("critic"),
        "data_timestamps": {"assessment_computed_at": (card or {}).get("computed_at"),
                            "data_time": (card or {}).get("data_time"), "model_run": (card or {}).get("model_run")},
    }
    run_state = {
        "assessment": card,
        "official_alerts": (final.get("official_alerts") or {}).get("alerts"),
        "retrieved_chunks": list(tools.retrieved.values()),
        "generic_chunks": list(tools.generic.values()),
        "facilities": list(tools.facilities.values()),
        "geocodes": list(tools.geocodes.values()),
        "unresolved_places": tools.unresolved_places,
        "routes": tools.routes,
        "not_found_queries": tools.not_found_queries,
        "errors": (final.get("errors") or []) + tools.errors,
        "retrieval_backend": tools.retrieval_backend,
    }
    comms = final.get("communications") or {}
    cfg = get_config()
    d = cfg.district(ctx.district_id)
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        run.status = status
        run.finished_at = utcnow()
        run.attempts = int(final.get("attempt") or 0)
        run.tool_calls = tools.calls
        run.tokens_in, run.tokens_out = ctx.tokens_in, ctx.tokens_out
        run.model = ", ".join(ctx.models_used) or run.model
        run.plan = plan
        run.verification = report or None
        run.state = run_state
        run.error = "; ".join(final.get("errors") or [])[:2000] or None
        actions = []
        for i, a in enumerate(draft.get("actions") or []):
            row = ActionItemRow(
                run_id=run_id, key=str(a.get("id") or f"A{i + 1}")[:16], phase=a.get("phase") or "Do now",
                action=a.get("action") or "", responsible=a.get("responsible"),
                location_refs=a.get("location_refs") or [], source=a.get("source") or "suggested",
                citations=_enrich_citations(a.get("citations") or [], tools), rationale=a.get("rationale") or "",
                problems=problems_by_action.get(a.get("id"), []) if status == "partial" else [], position=i,
            )
            s.add(row)
            actions.append(row)
        s.flush()
        draft_c = comms.get("draft") or {}
        trig = s.get(Trigger, final.get("trigger_id")) if final.get("trigger_id") else None
        district_row = s.get(District, ctx.district_id)
        if comms.get("drafted") and draft_c:
            extra = comms.get("problems") or []
            en_problems = sms.check_english(draft_c.get("sms_en", "")) + [
                x for x in extra if x.startswith(("sms_en", "English")) and x not in sms.check_english(draft_c.get("sms_en", ""))]
            ta_problems = sms.check_tamil(draft_c.get("sms_ta", "")) + [
                x for x in extra if x.startswith(("sms_ta", "Tamil")) and x not in sms.check_tamil(draft_c.get("sms_ta", ""))]
            s.add(Communication(run_id=run_id, kind="public_en", content=draft_c.get("sms_en", ""),
                                valid=not en_problems, validation={"problems": en_problems, **sms.count(draft_c.get("sms_en", "")).as_dict()}))
            s.add(Communication(run_id=run_id, kind="public_ta", content=draft_c.get("sms_ta", ""),
                                valid=not ta_problems, validation={"problems": ta_problems, **sms.count(draft_c.get("sms_ta", "")).as_dict()}))
            cap_xml, cap_val = build_run_cap(run_id, ctx.district_id, ctx.hazard, card, draft_c,
                                             trig.cap_msg_type if trig else "Alert", district_row)
            s.add(Communication(run_id=run_id, kind="cap", content=cap_xml, valid=cap_val["valid"],
                                validation={**cap_val, "fields": {k: draft_c.get(k) for k in
                                                                 ("headline_en", "description_en", "instruction_en",
                                                                  "headline_ta", "description_ta", "instruction_ta")}}))
        elif comms.get("reason"):
            plan["no_public_message_reason"] = comms["reason"]
            run.plan = plan
        briefing = render_html(
            district=d.name if d else ctx.district_id, state=cfg.state_name(d.state) if d else "",
            plan=plan, assessment=card,
            actions=[{"key": a.key, "phase": a.phase, "action": a.action, "responsible": a.responsible, "source": a.source,
                      "citations": a.citations, "status": a.status} for a in actions],
            comms={"public_en": draft_c.get("sms_en"), "public_ta": draft_c.get("sms_ta")} if draft_c else None,
            run_meta={"run_id": run_id, "model": run.model, "status": status},
        )
        s.add(Communication(run_id=run_id, kind="briefing", content=briefing, valid=True, validation={}))


def build_run_cap(run_id: str, district_id: str, hazard: str, card: dict | None, fields: dict, msg_type: str,
                  district_row: District | None, references: list[str] | None = None) -> tuple[str, dict]:
    head = (card or {}).get("headline") or {}
    p = None if head.get("probability_pct") is None else float(head["probability_pct"]) / 100.0
    cat = head.get("category")
    now = utcnow()
    onset = None
    if head.get("t_minus_h") is not None and head.get("earliest_onset_h") is not None:
        onset = now + dt.timedelta(hours=float(head["earliest_onset_h"]))
    expires = None
    if head.get("expected_impact_utc"):
        expires = dt.datetime.fromisoformat(head["expected_impact_utc"]) + dt.timedelta(hours=24)
    d_cfg = get_config().district(district_id)
    name = d_cfg.name if d_cfg else district_id
    level = (card or {}).get("level") or "QUIET"
    params = {"probability": f"{head.get('probability_pct')}% ({head.get('members_exceeding')} of {head.get('valid_members')} forecasts)"
              if p is not None else "n/a",
              "T-Minus level": level, "model": f"{(card or {}).get('model')} run {(card or {}).get('model_run')}",
              "prototype": "T-Minus exercise output — not an official warning"}
    common = dict(urgency=urgency(head.get("t_minus_h")), severity=severity(cat), certainty=certainty(p),
                  response_type=RESPONSE.get(level), onset=onset, expires=expires, effective=now,
                  sender_name=f"T-Minus prototype for {name} DDMA (exercise)")
    infos = [
        InfoBlock(language="en-IN", event=EVENT_NAME.get(cat, "Heavy rainfall"), headline=fields.get("headline_en") or "",
                  description=fields.get("description_en") or "", instruction=fields.get("instruction_en") or "",
                  parameters=params, **common),
        InfoBlock(language="ta-IN", event=EVENT_NAME.get(cat, "Heavy rainfall"), headline=fields.get("headline_ta") or "",
                  description=fields.get("description_ta") or "", instruction=fields.get("instruction_ta") or "", **common),
    ]
    poly = cap_polygon(district_row.boundary if district_row else None)
    circle = None if poly else (f"{d_cfg.hq[0]:.4f},{d_cfg.hq[1]:.4f} {get_config().search_radius_km(d_cfg):.0f}" if d_cfg else None)
    xml = build_cap(identifier=f"TMINUS-{district_id}-{hazard}-{run_id}-{int(now.timestamp())}",
                    sender=get_settings_cap_sender(), sent=now, msg_type=msg_type, infos=infos,
                    area=Area(desc=f"{name} district, {get_config().state_name(d_cfg.state) if d_cfg else ''}".strip(", "),
                              polygon=poly, circle=circle),
                    references=references, note="PROTOTYPE EXERCISE: T-Minus decision-support output, not an official warning.")
    return xml, validate_cap(xml)


def get_settings_cap_sender() -> str:
    return "tminus-prototype"
