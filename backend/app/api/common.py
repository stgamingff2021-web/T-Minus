"""Serialisers and queries shared by the API routers."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select

from app.config import get_config
from app.db.models import AgentRun, Assessment, District, ScanRun, Trigger
from app.geo import simplified_geojson
from app.rag import retrieve
from app.triggers.levels import max_level, rank


def iso(t: dt.datetime | None) -> str | None:
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.isoformat()


def aware(t: dt.datetime | None) -> dt.datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def latest_assessment(s, district_id: str, hazard: str) -> Assessment | None:
    return s.execute(select(Assessment).where(Assessment.district_id == district_id, Assessment.hazard == hazard)
                     .order_by(Assessment.computed_at.desc()).limit(1)).scalars().first()


def last_scan(s) -> ScanRun | None:
    return s.execute(select(ScanRun).where(ScanRun.finished_at.is_not(None))
                     .order_by(ScanRun.started_at.desc()).limit(1)).scalars().first()


def trigger_dict(t: Trigger, district_name: str | None = None) -> dict:
    return {
        "id": t.id, "district_id": t.district_id, "district": district_name, "hazard": t.hazard, "level": t.level,
        "status": t.status, "window_start": iso(t.window_start), "window_end": iso(t.window_end), "t_star": iso(t.t_star),
        "opened_at": iso(t.opened_at), "updated_at": iso(t.updated_at), "closed_at": iso(t.closed_at),
        "cap_msg_type": t.cap_msg_type, "history": t.history or [], "assessment_id": t.assessment_id,
        "below_runs": (t.state or {}).get("below_runs", []),
    }


def assessment_dict(a: Assessment | None, *, stale_scan: bool = False) -> dict | None:
    if a is None:
        return None
    max_age_h = float(get_config().hazards["rain"].get("max_data_age_h", 6))
    data_age_h = None
    if a.data_time:
        data_age_h = round((dt.datetime.now(dt.timezone.utc) - aware(a.data_time)).total_seconds() / 3600, 2)
    stale = bool(stale_scan or (data_age_h is not None and data_age_h > max_age_h))
    flags = a.flags or {}
    details = a.details or {}
    return {
        "id": a.id, "hazard": a.hazard, "computed_at": iso(a.computed_at), "data_time": iso(a.data_time),
        "data_age_h": data_age_h, "stale": stale, "insufficient": bool(a.insufficient or (data_age_h or 0) > max_age_h),
        "model": a.model, "model_run_time": iso(a.model_run_time), "model_run_id": a.model_run_id,
        "n_members": a.n_members, "n_valid": a.n_valid, "degraded": a.degraded,
        "level": a.level, "base_level": a.base_level, "hysteresis_level": a.hysteresis_level,
        "t_star": iso(a.t_star), "t_minus_h": a.t_minus_h, "onset_time": iso(a.onset_time), "onset_h": a.onset_h,
        "probabilities": a.probabilities or {}, "headline": details.get("headline"), "lead": details.get("lead"),
        "rules_met": details.get("rules_met"), "flags": flags, "fetch_ids": a.fetch_ids or [],
        "grid": details.get("grid"), "points": details.get("points"), "caveat": details.get("caveat"),
    }


def district_summary(s, d_row: District, failed_ids: set[str], with_geometry: bool = False) -> dict:
    cfg = get_config()
    d_cfg = cfg.district(d_row.id)
    rain = latest_assessment(s, d_row.id, "rain")
    river = latest_assessment(s, d_row.id, "river")
    trigs = s.execute(select(Trigger).where(Trigger.district_id == d_row.id, Trigger.status != "closed")
                      .order_by(Trigger.opened_at.desc())).scalars().all()
    active_levels = [t.level for t in trigs if t.status == "active"]
    level = max_level(*active_levels) if active_levels else max_level(rain.level if rain else None,
                                                                       river.level if river else None)
    drivers = [t.hazard for t in trigs if t.status == "active" and t.level == level] or \
              [h for h, a in (("rain", rain), ("river", river)) if a is not None and a.level == level and level]
    level_hazard = drivers[0] if drivers else None
    rain_d = assessment_dict(rain, stale_scan=d_row.id in failed_ids)
    head = (rain_d or {}).get("headline") or {}
    top = max(trigs, key=lambda t: (rank(t.level), -(aware(t.t_star).timestamp() if t.t_star else 9e12)), default=None)
    driver = river if level_hazard == "river" else rain
    t_star = iso(top.t_star) if top and top.t_star else (iso(driver.t_star) if driver is not None and driver.t_star else None)
    flags = (rain_d or {}).get("flags") or {}
    out = {
        "id": d_row.id, "name": d_row.name, "state": d_row.state, "hq": [d_row.centroid_lat, d_row.centroid_lon],
        "aliases": d_row.aliases or [], "level": level, "level_hazard": level_hazard,
        "river_detail": _river_detail(river) if river is not None and river.level else None,
        "triggers": [trigger_dict(t) for t in trigs],
        "stand_down_recommended": any(t.status == "stand_down_recommended" for t in trigs),
        "t_star": t_star,
        "headline": head or None,
        "rain": {k: (rain_d or {}).get(k) for k in ("id", "level", "model", "model_run_id", "data_time", "data_age_h",
                                                    "stale", "insufficient", "degraded", "n_members", "n_valid",
                                                    "t_minus_h", "onset_h")} if rain_d else None,
        "river_level": river.level if river else None,
        "official": [{"color": a.get("color"), "agency": a.get("agency"), "event": a.get("event")}
                     for a in flags.get("official_alerts") or []],
        "official_unavailable": flags.get("official_available") is False,
        "disagreement": bool(flags.get("disagreement")),
        "escalations": flags.get("escalations") or [],
        "ensemble_unavailable": bool(flags.get("ensemble_unavailable")),
        "has_plan": retrieve.district_has_plan(d_row.id),
        "has_boundary": d_row.boundary is not None,
        "watchlist": bool(d_cfg and d_cfg.state != "TN"),
    }
    if with_geometry:
        out["geometry"] = simplified_geojson(d_row.boundary) if d_row.boundary else None
    return out


def _river_detail(a: Assessment) -> dict | None:
    for p in (a.details or {}).get("points") or []:
        rule = (p.get("rules") or {}).get(a.level or "")
        if p.get("level") == a.level and rule:
            return {"river": p.get("river"), "statistic": rule["statistic"], "percentile": rule["percentile"],
                    "threshold_m3s": rule["threshold_m3s"], "peak_m3s": rule["peak_m3s"],
                    "first_exceed_time": rule.get("first_exceed_time")}
    return None


def sort_key(d: dict) -> tuple:
    ts = d.get("t_star")
    t = dt.datetime.fromisoformat(ts).timestamp() if ts else 9e12
    return (-rank(d.get("level")), t, d["name"])


def run_brief(r: AgentRun) -> dict[str, Any]:
    return {"id": r.id, "district_id": r.district_id, "hazard": r.hazard, "trigger_id": r.trigger_id,
            "status": r.status, "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
            "started_by": r.started_by, "model": r.model, "attempts": r.attempts, "tool_calls": r.tool_calls,
            "level": (r.plan or {}).get("level")}
