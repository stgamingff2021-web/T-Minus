"""Districts, scans, triggers, assessments and raw-fetch traceability."""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process
from sqlalchemy import select

from app import scanner
from app.api.common import (
    assessment_dict,
    district_summary,
    iso,
    last_scan,
    latest_assessment,
    run_brief,
    sort_key,
    trigger_dict,
)
from app.auth import User, current_user
from app.config import get_config
from app.db.models import AgentRun, Approval, Assessment, District, PlanDocument, SourceFetch, Trigger, utcnow
from app.db.session import session_scope
from app.errors import APIError
from app.hazards.ensemble_math import exceedance, rolling_accumulation
from app.hazards.rain import futures_series
from app.ratelimit import limit
from app.sources.open_meteo import parse_members

router = APIRouter(prefix="/api", tags=["districts"])


def _failed_ids(s) -> set[str]:
    ls = last_scan(s)
    return set((ls.summary or {}).get("districts_failed") or []) if ls else set()


@router.get("/districts")
def list_districts(geometry: bool = False):
    with session_scope() as s:
        failed = _failed_ids(s)
        rows = s.execute(select(District)).scalars().all()
        items = sorted((district_summary(s, r, failed, with_geometry=geometry) for r in rows), key=sort_key)
        ls = last_scan(s)
        above = [d for d in items if d["level"]]
        return {
            "districts": items,
            "last_scan": {"id": ls.id, "finished_at": iso(ls.finished_at), "status": ls.status,
                          "summary": {k: (ls.summary or {}).get(k) for k in ("levels", "districts_ok", "districts_failed",
                                                                            "duration_s", "errors", "models_used")}}
            if ls else None,
            "all_clear": bool(ls) and not above and ls.status in ("ok", "partial") and
                         (ls.summary or {}).get("districts_ok", 0) > 0,
            "scan_running": scanner.scan_state().get("running", False),
        }


@router.get("/districts/resolve")
def resolve_district(q: str = Query(..., min_length=1, max_length=80)):
    """Fuzzy-match a district name or alias against the official list (§8.3)."""
    cfg = get_config()
    choices = {}
    for d in cfg.districts:
        for n in d.all_names():
            choices[n] = d.id
    best = process.extract(q, list(choices), scorer=fuzz.WRatio, limit=8)
    matches, seen = [], set()
    for name, score, _ in best:
        did = choices[name]
        if score < 60 or did in seen:
            continue
        seen.add(did)
        matches.append({"id": did, "name": cfg.district(did).name, "matched": name, "score": round(score, 1)})
    matches = matches[:3]
    exact = matches and matches[0]["score"] >= 97
    return {"query": q, "matches": matches, "exact": bool(exact),
            "message": None if matches else f"No monitored district matches '{q}'."}


@router.get("/districts/{district_id}")
def district_detail(district_id: str):
    cfg = get_config()
    d_cfg = cfg.district(district_id)
    if d_cfg is None:
        raise APIError(404, "unknown_district", f"Unknown district '{district_id}'. Use /api/districts/resolve?q=… to find it.")
    with session_scope() as s:
        failed = _failed_ids(s)
        row = s.get(District, district_id)
        summary = district_summary(s, row, failed, with_geometry=True)
        rain = latest_assessment(s, district_id, "rain")
        river = latest_assessment(s, district_id, "river")
        trigs = s.execute(select(Trigger).where(Trigger.district_id == district_id)
                          .order_by(Trigger.opened_at.desc()).limit(10)).scalars().all()
        runs = s.execute(select(AgentRun).where(AgentRun.district_id == district_id)
                         .order_by(AgentRun.started_at.desc()).limit(10)).scalars().all()
        docs = s.execute(select(PlanDocument).where(PlanDocument.district_id == district_id)).scalars().all()
        history = s.execute(select(Assessment).where(Assessment.district_id == district_id, Assessment.hazard == "rain")
                            .order_by(Assessment.computed_at.desc()).limit(40)).scalars().all()
        out: dict[str, Any] = {
            "district": summary,
            "rain": assessment_dict(rain, stale_scan=district_id in failed),
            "river": assessment_dict(river),
            "triggers": [trigger_dict(t, row.name) for t in trigs],
            "runs": [run_brief(r) for r in runs],
            "plan_documents": [{"id": d.id, "title": d.title, "kind": d.kind, "pages": d.pages,
                                "edition_year": d.edition_year, "source_url": d.source_url} for d in docs],
            "history": [{"computed_at": iso(a.computed_at), "level": a.level,
                         "probability": ((a.details or {}).get("headline") or {}).get("probability"),
                         "model_run_id": a.model_run_id} for a in reversed(history)],
            "river_caveat": cfg.hazards["river"].get("caveat"),
            "futures": None,
        }
        if rain is not None and rain.fetch_ids and (rain.details or {}).get("point_index") is not None:
            out["futures"] = _futures(s, rain)
    return out


def _load_point(s, a: Assessment):
    fetch = s.get(SourceFetch, a.fetch_ids[0])
    if fetch is None or fetch.raw_payload is None:
        return None, None
    payload = fetch.raw_payload
    items = payload if isinstance(payload, list) else [payload]
    idx = int((a.details or {}).get("point_index") or 0)
    if idx >= len(items):
        return None, fetch
    return parse_members(items[idx], "precipitation"), fetch


def _futures(s, a: Assessment) -> dict | None:
    pt, fetch = _load_point(s, a)
    if pt is None:
        return None
    now = (a.details or {}).get("now") or a.computed_at.timestamp()
    series = futures_series(pt, get_config().hazards["rain"], float(now))
    series.update({"fetch_id": fetch.id, "fetched_at": iso(fetch.fetched_at), "model": a.model,
                   "model_run_id": a.model_run_id})
    return series


@router.get("/assessments/{assessment_id}/recompute")
def recompute(assessment_id: str):
    """AC-02: recompute every displayed probability by hand from the stored raw response."""
    cfg = get_config().hazards["rain"]
    with session_scope() as s:
        a = s.get(Assessment, assessment_id)
        if a is None:
            raise APIError(404, "not_found", "Assessment not found")
        if a.hazard != "rain":
            raise APIError(400, "unsupported", "Recompute is available for rain assessments.")
        pt, fetch = _load_point(s, a)
        if pt is None:
            raise APIError(410, "raw_payload_unavailable", "The raw payload for this assessment is no longer stored.")
        now = float((a.details or {}).get("now") or a.computed_at.timestamp())
        acc = rolling_accumulation(pt.members, int(cfg.get("accumulation_window_h", 24)))
        rows = []
        for key, stored in (a.probabilities or {}).items():
            ex = exceedance(acc, pt.times, float(stored["threshold_mm"]), now, float(stored["within_h"]))
            start, end = ex.window_start, ex.window_end
            sel = (pt.times >= (start or 0)) & (pt.times <= (end or 0))
            maxima = [None if np.all(np.isnan(r[sel])) or np.any(np.isnan(r[sel])) else round(float(np.max(r[sel])), 2)
                      for r in acc] if sel.any() else []
            rows.append({
                "key": key, "threshold_mm": stored["threshold_mm"], "within_h": stored["within_h"],
                "stored": {"n_exceed": stored["n_exceed"], "n_valid": stored["n_valid"], "probability": stored["probability"]},
                "recomputed": {"n_exceed": ex.n_exceed, "n_valid": ex.n_valid, "probability": ex.probability},
                "match": ex.n_exceed == stored["n_exceed"] and ex.n_valid == stored["n_valid"],
                "member_max_24h_mm": dict(zip(pt.member_names, maxima)),
            })
        return {"assessment_id": a.id, "district_id": a.district_id, "model": a.model,
                "fetch": {"id": fetch.id, "url": fetch.url, "fetched_at": iso(fetch.fetched_at),
                          "sha256": fetch.payload_sha256, "bytes": fetch.payload_bytes, "params": fetch.request_params},
                "formula": "P = (# valid members whose max 24-h total within the window >= threshold) / (# valid members)",
                "rows": rows, "all_match": all(r["match"] for r in rows)}


@router.get("/fetches/{fetch_id}")
def get_fetch(fetch_id: str, include_payload: bool = False):
    with session_scope() as s:
        f = s.get(SourceFetch, fetch_id)
        if f is None:
            raise APIError(404, "not_found", "Fetch not found")
        return {"id": f.id, "source": f.source, "url": f.url, "params": f.request_params, "fetched_at": iso(f.fetched_at),
                "status": f.status, "http_code": f.http_code, "latency_ms": f.latency_ms, "attempts": f.attempts,
                "sha256": f.payload_sha256, "bytes": f.payload_bytes, "error": f.error, "simulated": f.simulated,
                "model_run_id": f.model_run_id, "payload": f.raw_payload if include_payload else None}


@router.get("/fetches")
def list_fetches(source: str | None = None, limit_n: int = Query(50, alias="limit", le=500)):
    with session_scope() as s:
        q = select(SourceFetch).order_by(SourceFetch.fetched_at.desc()).limit(limit_n)
        if source:
            q = q.where(SourceFetch.source == source)
        return {"fetches": [{"id": f.id, "source": f.source, "fetched_at": iso(f.fetched_at), "status": f.status,
                             "http_code": f.http_code, "latency_ms": f.latency_ms, "error": f.error,
                             "simulated": f.simulated, "bytes": f.payload_bytes, "url": f.url}
                            for f in s.execute(q).scalars()]}


@router.post("/scan", status_code=202, dependencies=[Depends(limit(capacity=3, refill_per_s=1 / 60))])
async def scan_now():
    if scanner.scan_state().get("running"):
        return {"status": "already_running"}
    asyncio.get_running_loop().create_task(scanner.run_scan("manual"))
    return {"status": "started", "started_at": utcnow().isoformat()}


@router.get("/triggers")
def list_triggers(status: str | None = Query(None, pattern="^(active|stand_down_recommended|closed|open)$")):
    with session_scope() as s:
        q = select(Trigger).order_by(Trigger.updated_at.desc())
        if status == "open":
            q = q.where(Trigger.status != "closed")
        elif status:
            q = q.where(Trigger.status == status)
        names = {d.id: d.name for d in s.execute(select(District)).scalars()}
        return {"triggers": [trigger_dict(t, names.get(t.district_id)) for t in s.execute(q.limit(200)).scalars()]}


class CloseBody(BaseModel):
    confirm: bool = Field(..., description="Must be true: the officer confirms the stand-down")
    note: str | None = Field(None, max_length=500)


@router.post("/triggers/{trigger_id}/close", dependencies=[Depends(limit())])
def close_trigger(trigger_id: str, body: CloseBody, user: User = Depends(current_user)):
    if not body.confirm:
        raise APIError(400, "confirmation_required", "Closing a trigger requires explicit confirmation.")
    with session_scope() as s:
        t = s.get(Trigger, trigger_id)
        if t is None:
            raise APIError(404, "not_found", "Trigger not found")
        if t.status == "closed":
            raise APIError(409, "already_closed", "Trigger is already closed.")
        before = f"{t.level}/{t.status}"
        t.status, t.closed_at, t.updated_at, t.cap_msg_type = "closed", utcnow(), utcnow(), "Cancel"
        t.history = [*(t.history or []), {"ts": utcnow().isoformat(), "action": "closed_by_officer", "level": t.level,
                                          "status": "closed", "reason": body.note or "Officer confirmed stand-down",
                                          "user": user.name}]
        s.add(Approval(target_type="trigger", target_id=t.id, decision="close", before=before, after="closed",
                       user=user.name, role=user.role, note=body.note))
        cancel = _publish_cancel(s, t, user, body.note)
        out = trigger_dict(t)
        out["cancel_cap"] = cancel
        return out


def _publish_cancel(s, t: Trigger, user: User, note: str | None) -> dict | None:
    """Officer-confirmed stand-down: if an alert was published for this event, publish a CAP Cancel."""
    from app.comms.cap import Area, InfoBlock, build_cap, validate_cap
    from app.db.models import AgentRun, Communication, PublicAlert
    from app.geo import cap_polygon

    prev = s.execute(select(PublicAlert).where(PublicAlert.district_id == t.district_id, PublicAlert.hazard == t.hazard,
                                               PublicAlert.published_at >= t.opened_at)
                     .order_by(PublicAlert.published_at.desc())).scalars().first()
    if prev is None or prev.msg_type == "Cancel":
        return None
    d_cfg = get_config().district(t.district_id)
    d_row = s.get(District, t.district_id)
    prev_cap = s.get(Communication, prev.cap_communication_id) if prev.cap_communication_id else None
    import re as _re

    def grab(tag):
        m = _re.search(rf"<{tag}>([^<]+)</{tag}>", prev_cap.content if prev_cap else "")
        return m.group(1) if m else None

    refs = [",".join([grab("sender"), grab("identifier"), grab("sent")])] if prev_cap and all(
        [grab("sender"), grab("identifier"), grab("sent")]) else None
    now = utcnow()
    msg_en = f"Stand-down: the earlier T-Minus exercise alert for {d_cfg.name} is cancelled by the district officer."
    msg_ta = f"{d_cfg.name}: முந்தைய எச்சரிக்கை (பயிற்சி) மாவட்ட அதிகாரியால் ரத்து செய்யப்பட்டது."
    info = dict(event="Rainfall alert cancelled", urgency="Past", severity="Minor", certainty="Unlikely", effective=now,
                sender_name=f"T-Minus prototype for {d_cfg.name} DDMA (exercise)")
    poly = cap_polygon(d_row.boundary if d_row else None)
    xml = build_cap(identifier=f"TMINUS-{t.district_id}-{t.hazard}-{t.id}-cancel-{int(now.timestamp())}",
                    sender="tminus-prototype", sent=now, msg_type="Cancel",
                    infos=[InfoBlock("en-IN", headline="Alert cancelled (stand-down)", description=msg_en,
                                     instruction=note or "No further action required.", **info),
                           InfoBlock("ta-IN", headline="எச்சரிக்கை ரத்து", description=msg_ta,
                                     instruction=note or "மேலும் நடவடிக்கை தேவையில்லை.", **info)],
                    area=Area(desc=f"{d_cfg.name} district", polygon=poly,
                              circle=None if poly else f"{d_cfg.hq[0]:.4f},{d_cfg.hq[1]:.4f} {get_config().search_radius_km(d_cfg):.0f}"),
                    references=refs, note="PROTOTYPE EXERCISE: T-Minus decision-support output, not an official warning.")
    val = validate_cap(xml)
    if not val["valid"]:
        return {"valid": False, "errors": val["errors"]}
    comm = Communication(run_id=prev.run_id, kind="cap", content=xml, valid=True, status="published",
                         validation={**val, "msg_type": "Cancel"})
    s.add(comm)
    s.flush()
    s.add(PublicAlert(run_id=prev.run_id, district_id=t.district_id, hazard=t.hazard, level=t.level, msg_type="Cancel",
                      message_en=msg_en, message_ta=msg_ta, cap_communication_id=comm.id, published_by=user.name,
                      data_timestamps={}))
    s.add(Approval(target_type="cap", target_id=comm.id, decision="publish_cancel", before=None, after=xml[:4000],
                   user=user.name, role=user.role, note=note))
    return {"valid": True, "communication_id": comm.id}
