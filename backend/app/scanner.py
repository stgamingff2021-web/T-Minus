"""Scanner: live data -> assessments -> resolved levels -> trigger lifecycle (spec §6.1 steps 1–2).

One scan:
  1. Rain: ECMWF ensemble for every district HQ (batched); per batch, fall back to the
     next ensemble model (GFS) if a model fails — recorded in the Sources panel.
  2. Antecedent rain (past 72 h) from the Forecast API -> saturated-ground escalation.
  3. River: GloFAS forecast for every river point vs. its stored P95/P99 baseline.
  4. Official alerts from SACHET -> floors and disagreement check.
  5. Per district: resolve the level (floors, +1 escalation cap, disagreement), persist
     the assessment, then apply the trigger lifecycle (dedupe, hysteresis by model run).
  6. Fire the agent automatically for triggers that open or escalate (>= auto level).

Nothing is invented on failure: a district whose rain data failed keeps its last real
assessment, which the API shows as STALE with its timestamp; no trigger is opened or
escalated on data older than ``max_data_age_h``.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any

import numpy as np
from sqlalchemy import select

from app import notify
from app.config import DistrictCfg, get_config, get_settings
from app.db.models import (
    Assessment,
    District,
    MonitorPoint,
    RiverBaseline,
    ScanRun,
    Trigger,
    utcnow,
)
from app.db.session import session_scope
from app.geo import load_boundaries
from app.hazards.ensemble_math import accumulate_past, floor_hour
from app.hazards.rain import assess_rain
from app.hazards.river import assess_river_point, percentiles, river_chart
from app.sources import registry
from app.sources.base import SourceError
from app.sources.open_meteo import (
    EnsemblePoint,
    fetch_ensemble,
    fetch_flood_forecast,
    fetch_flood_reanalysis,
    fetch_model_run_time,
    fetch_past_rain,
)
from app.sources.sachet import OfficialAlert, district_alerts, fetch_alerts, strongest_color
from app.triggers.escalation import resolve_level
from app.triggers.levels import max_level, rank
from app.triggers.lifecycle import Observation, TriggerSnapshot, same_event, step

log = logging.getLogger("tminus.scanner")
_scan_lock = asyncio.Lock()
_state: dict[str, Any] = {"last_scan": None, "running": False, "last_error": None}
_alerts_cache: dict[str, Any] = {"at": 0.0, "alerts": [], "ok": False, "error": None, "fetch_id": None}
BATCH = 10


def _ts(t: float | int | None) -> dt.datetime | None:
    return None if t is None else dt.datetime.fromtimestamp(float(t), tz=dt.timezone.utc)


def _chunks(seq: list, n: int) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def scan_state() -> dict[str, Any]:
    return dict(_state)


# ---------------------------------------------------------------------------
# District / monitor-point bootstrap
# ---------------------------------------------------------------------------

def sync_districts() -> None:
    """Mirror config/districts.yaml (+ downloaded boundaries) into the database."""
    cfg = get_config()
    boundaries, prov = load_boundaries()
    with session_scope() as s:
        for d in cfg.districts:
            row = s.get(District, d.id) or District(id=d.id)
            row.name, row.state, row.aliases = d.name, cfg.state_name(d.state), list(d.aliases)
            row.centroid_lat, row.centroid_lon = d.hq
            if d.id in boundaries:
                row.boundary = boundaries[d.id]
                row.boundary_source = (prov or {}).get("source")
            s.merge(row)
            pid = f"{d.id}:hq"
            if not s.get(MonitorPoint, pid):
                s.add(MonitorPoint(id=pid, district_id=d.id, kind="centroid", lat=d.hq[0], lon=d.hq[1]))
            for i, r in enumerate(d.rivers):
                rid = f"{d.id}:river{i}"
                mp = s.get(MonitorPoint, rid)
                if not mp:
                    s.add(MonitorPoint(id=rid, district_id=d.id, kind="river", lat=r.lat, lon=r.lon, river_name=r.name))
                elif (mp.lat, mp.lon) != (r.lat, r.lon):
                    mp.lat, mp.lon, mp.effective_lat, mp.effective_lon, mp.on_modelled_river = r.lat, r.lon, None, None, None


# ---------------------------------------------------------------------------
# Model-run identity (hysteresis counts NEW runs, never re-scans of the same run)
# ---------------------------------------------------------------------------

def run_signature(pt: EnsemblePoint, now: float) -> dict[str, float]:
    sel = np.where(pt.times >= floor_hour(now))[0][::6][:40]
    out = {}
    for i in sel:
        col = pt.members[:, i]
        if np.all(np.isnan(col)):
            continue
        out[str(int(pt.times[i]))] = round(float(np.nanmean(col)), 4)
    return out


def same_run(a: dict | None, b: dict | None) -> bool | None:
    if not a or not b:
        return None
    common = set(a) & set(b)
    if len(common) < 4:
        return None
    return all(abs(a[k] - b[k]) < 1e-3 for k in common)


def _previous_assessment(s, district_id: str, hazard: str) -> Assessment | None:
    return s.execute(
        select(Assessment).where(Assessment.district_id == district_id, Assessment.hazard == hazard)
        .order_by(Assessment.computed_at.desc()).limit(1)
    ).scalars().first()


# ---------------------------------------------------------------------------
# Fetch stages
# ---------------------------------------------------------------------------

async def _fetch_rain(districts: list[DistrictCfg]) -> tuple[dict[str, dict], dict[str, list[str]], dict[str, Any]]:
    """Returns ({district_id: {model, point, fetch_id, index}}, {district_id: errors}, run_meta)."""
    models = get_settings().ensemble_model_list
    out: dict[str, dict] = {}
    errors: dict[str, list[str]] = {}
    run_meta: dict[str, Any] = {}
    sem = asyncio.Semaphore(3)

    async def one(batch: list[DistrictCfg]) -> None:
        errs: list[str] = []
        async with sem:
            for model in models:
                try:
                    eb = await fetch_ensemble([d.hq for d in batch], model)
                except SourceError as exc:
                    errs.append(f"{model}: {exc.message}")
                    continue
                for i, (d, pt) in enumerate(zip(batch, eb.points)):
                    out[d.id] = {"model": model, "point": pt, "fetch_id": eb.fetch.fetch_id, "index": i,
                                 "fallback_from": list(errs)}
                return
        for d in batch:
            errors[d.id] = errs

    await asyncio.gather(*(one(b) for b in _chunks(districts, BATCH)))
    for model in {v["model"] for v in out.values()}:
        init, _ = await fetch_model_run_time(model)
        run_meta[model] = init
        if init:
            registry.set_model_run(f"ensemble:{model}", _ts(init).strftime("%Y-%m-%d %H:%MZ"))
    return out, errors, run_meta


async def _fetch_antecedent(districts: list[DistrictCfg], now: float) -> tuple[dict[str, dict], str | None]:
    out: dict[str, dict] = {}
    err = None
    for batch in _chunks(districts, 25):
        try:
            res, pts = await fetch_past_rain([d.hq for d in batch])
        except SourceError as exc:
            err = exc.message
            continue
        for d, pt in zip(batch, pts):
            total, n = accumulate_past(pt.values, pt.times, now, 72)
            out[d.id] = {"mm_72h": None if total is None else round(total, 1), "hours": n, "fetch_id": res.fetch_id}
    return out, err


def _river_points() -> list[MonitorPoint]:
    with session_scope() as s:
        return list(s.execute(select(MonitorPoint).where(MonitorPoint.kind == "river")).scalars())


def _baselines() -> dict[str, RiverBaseline]:
    with session_scope() as s:
        return {b.point_id: b for b in s.execute(select(RiverBaseline)).scalars()}


async def ensure_river_baselines(limit: int | None = None) -> dict[str, Any]:
    """Fetch GloFAS reanalysis once per river point and store P95/P99 with provenance (§6.4)."""
    cfg = get_config().hazards["river"]["baseline"]
    done, failed = [], []
    have = _baselines()
    todo = [p for p in _river_points() if p.id not in have and p.on_modelled_river is not False]
    if limit is not None:
        todo = todo[:limit]
    for mp in todo:
        lat, lon = (mp.effective_lat or mp.lat), (mp.effective_lon or mp.lon)
        try:
            res, fp = await fetch_flood_reanalysis(lat, lon, cfg["start"], cfg["end"])
            p95, p99, n = percentiles(fp.series["river_discharge"])
        except (SourceError, ValueError) as exc:
            failed.append({"point": mp.id, "error": getattr(exc, "message", str(exc))})
            continue
        with session_scope() as s:
            s.merge(RiverBaseline(point_id=mp.id, p95=p95, p99=p99, n_days=n, period_start=cfg["start"],
                                  period_end=cfg["end"], fetch_id=res.fetch_id, lat=lat, lon=lon))
        done.append(mp.id)
    return {"computed": done, "failed": failed}


async def _fetch_river(now: float) -> tuple[dict[str, list[dict]], str | None]:
    """{district_id: [per-point assessment]} for points with a baseline."""
    points = _river_points()
    baselines = _baselines()
    levels_cfg = get_config().hazards["river"]["levels"]
    nudge = float(get_config().hazards["river"].get("nudge_deg", 0.1))
    out: dict[str, list[dict]] = {}
    err = None
    usable = [p for p in points if p.on_modelled_river is not False]
    for batch in _chunks(usable, 20):
        coords = [((p.effective_lat or p.lat), (p.effective_lon or p.lon)) for p in batch]
        try:
            res, fps = await fetch_flood_forecast(coords)
        except SourceError as exc:
            err = exc.message
            continue
        for mp, fp in zip(batch, fps):
            if fp.all_null() and mp.effective_lat is None:
                fp, coords_used = await _nudge(mp, nudge)
                if fp is None:
                    continue
            b = baselines.get(mp.id)
            entry: dict[str, Any] = {"point_id": mp.id, "river": mp.river_name, "lat": fp.lat, "lon": fp.lon,
                                     "fetch_id": res.fetch_id, "chart": river_chart(fp)}
            if b is None:
                entry.update({"level": None, "baseline": None, "note": "baseline pending"})
            else:
                entry.update(assess_river_point(fp, b.p95, b.p99, levels_cfg, now))
                entry["baseline"] = {"p95": b.p95, "p99": b.p99, "period": f"{b.period_start}..{b.period_end}",
                                     "n_days": b.n_days, "fetch_id": b.fetch_id}
            out.setdefault(mp.district_id, []).append(entry)
    return out, err


async def _nudge(mp: MonitorPoint, nudge: float):
    """Try +/- nudge offsets for a point with null discharge (§8.1)."""
    cands = [(mp.lat + dy, mp.lon + dx) for dy, dx in
             [(nudge, 0), (-nudge, 0), (0, nudge), (0, -nudge), (nudge, nudge), (-nudge, -nudge), (nudge, -nudge), (-nudge, nudge)]]
    try:
        _, fps = await fetch_flood_forecast(cands)
    except SourceError:
        return None, None
    for (lat, lon), fp in zip(cands, fps):
        if not fp.all_null():
            with session_scope() as s:
                row = s.get(MonitorPoint, mp.id)
                row.effective_lat, row.effective_lon, row.on_modelled_river = lat, lon, True
            return fp, (lat, lon)
    with session_scope() as s:
        s.get(MonitorPoint, mp.id).on_modelled_river = False
    return None, None


async def get_alerts(max_age_s: int = 600) -> dict[str, Any]:
    """SACHET alerts, cached 10 minutes (§9.5)."""
    if time.time() - _alerts_cache["at"] < max_age_s and (_alerts_cache["ok"] or _alerts_cache["error"]):
        return _alerts_cache
    try:
        alerts, fid = await fetch_alerts()
        _alerts_cache.update(at=time.time(), alerts=alerts, ok=True, error=None, fetch_id=fid)
    except SourceError as exc:
        _alerts_cache.update(at=time.time(), ok=False, error=exc.message,
                             alerts=_alerts_cache.get("alerts") or [], fetch_id=exc.fetch_id)
    return _alerts_cache


# ---------------------------------------------------------------------------
# Per-district resolution + lifecycle
# ---------------------------------------------------------------------------

def _rain_window(rain: dict | None, now: float, fired_within_h: float | None) -> tuple[dt.datetime, dt.datetime]:
    head = (rain or {}).get("headline") or {}
    start = _ts(head.get("onset")) or _ts(now)
    if head.get("t_star"):
        end = _ts(head["t_star"]) + dt.timedelta(hours=24)
    else:
        end = _ts(now) + dt.timedelta(hours=fired_within_h or 72)
    return start, end


def apply_lifecycle(s, district_id: str, hazard: str, assessment: Assessment, obs: Observation) -> dict | None:
    cfg = get_config().hazards["rain"]["stand_down"]
    runs_required = int(cfg.get("consecutive_model_runs", 2))
    existing = s.execute(
        select(Trigger).where(Trigger.district_id == district_id, Trigger.hazard == hazard,
                              Trigger.status != "closed").order_by(Trigger.opened_at.desc()).limit(1)
    ).scalars().first()
    now = utcnow()
    if existing is not None and not same_event(existing.window_start, existing.window_end,
                                               obs.window_start, obs.window_end):
        ended = existing.window_end is not None and _aware(existing.window_end) < now
        if ended and existing.status == "active":
            existing.status = "stand_down_recommended"
            existing.updated_at = now
            existing.history = [*existing.history, _hist("event_window_passed", existing.level, existing.status,
                                                          "Event window has passed; stand-down recommended.", assessment)]
        if ended:
            existing = None
    snap = None if existing is None else TriggerSnapshot(
        id=existing.id, level=existing.level, status=existing.status, window_start=existing.window_start,
        window_end=existing.window_end, below_runs=list((existing.state or {}).get("below_runs", [])),
    )
    d = step(snap, obs, runs_required)
    if not d.changes_trigger:
        return None
    if d.action == "open":
        t = Trigger(district_id=district_id, hazard=hazard, level=d.level, status="active",
                    window_start=obs.window_start, window_end=obs.window_end, t_star=assessment.t_star,
                    assessment_id=assessment.id, cap_msg_type="Alert", state={"below_runs": []},
                    history=[_hist("open", d.level, "active", d.reason, assessment)])
        s.add(t)
        s.flush()
        return {"trigger_id": t.id, "action": "open", "level": d.level, "district_id": district_id, "hazard": hazard}
    assert existing is not None
    prev_level = existing.level
    if d.level:
        existing.level = d.level
    if d.status:
        existing.status = d.status
        if d.status == "closed":
            existing.closed_at = now
    if d.cap_msg_type:
        existing.cap_msg_type = d.cap_msg_type
    existing.state = {**(existing.state or {}), "below_runs": d.below_runs}
    if d.action in ("open", "upgrade", "downgrade", "reactivate"):
        existing.window_start = obs.window_start or existing.window_start
        existing.window_end = obs.window_end or existing.window_end
        existing.t_star = assessment.t_star or existing.t_star
        existing.assessment_id = assessment.id
    elif d.action in ("count_below", "reset_below"):
        existing.assessment_id = assessment.id
    existing.updated_at = now
    existing.history = [*existing.history, _hist(d.action, existing.level, existing.status, d.reason, assessment)]
    return {"trigger_id": existing.id, "action": d.action, "level": existing.level, "previous_level": prev_level,
            "district_id": district_id, "hazard": hazard}


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _hist(action: str, level: str | None, status: str, reason: str, a: Assessment) -> dict:
    return {"ts": utcnow().isoformat(), "action": action, "level": level, "status": status, "reason": reason,
            "assessment_id": a.id, "model_run_id": a.model_run_id}


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

async def run_scan(kind: str = "manual") -> dict[str, Any]:
    if _scan_lock.locked():
        return {"status": "already_running", "last_scan": _state.get("last_scan")}
    async with _scan_lock:
        _state["running"] = True
        t0 = time.perf_counter()
        with session_scope() as s:
            scan = ScanRun(trigger=kind)
            s.add(scan)
            s.flush()
            scan_id = scan.id
        try:
            summary = await _scan_body(scan_id)
            if not summary.get("districts_failed"):
                status = "ok"
            elif summary.get("districts_ok"):
                status = "partial"
            else:
                status = "failed"  # nothing real could be fetched; last readings stay STALE
            err = None
        except Exception as exc:  # noqa: BLE001 - the health endpoint reports scan failures
            log.exception("scan failed")
            summary, status, err = {"error": str(exc)}, "failed", str(exc)
        summary["duration_s"] = round(time.perf_counter() - t0, 2)
        with session_scope() as s:
            row = s.get(ScanRun, scan_id)
            row.finished_at, row.status, row.summary, row.error = utcnow(), status, summary, err
        _state.update(running=False, last_error=err,
                      last_scan={"id": scan_id, "finished_at": utcnow().isoformat(), "status": status, **summary})
        notify.publish("system", {"type": "scan_finished", "scan_id": scan_id, "status": status,
                                  "summary": {k: summary.get(k) for k in ("levels", "duration_s", "districts_ok",
                                                                          "districts_failed")}})
        return _state["last_scan"]


async def _scan_body(scan_id: str) -> dict[str, Any]:
    cfg = get_config()
    rain_cfg = cfg.hazards["rain"]
    districts = cfg.districts
    now = time.time()

    rain_task = asyncio.create_task(_fetch_rain(districts))
    ante_task = asyncio.create_task(_fetch_antecedent(districts, now))
    alerts_task = asyncio.create_task(get_alerts())
    asyncio.create_task(ensure_river_baselines(limit=4))  # opportunistic; never blocks the scan
    river_task = asyncio.create_task(_fetch_river(now))
    (rain, rain_err, run_meta), (ante, ante_err), alerts_state, (river, river_err) = await asyncio.gather(
        rain_task, ante_task, alerts_task, river_task)

    alerts_ok: bool = bool(alerts_state["ok"])
    alerts: list[OfficialAlert] = alerts_state["alerts"] if alerts_ok else []
    floor_cfg = rain_cfg.get("official_alert_floor", {})
    esc_cfg = rain_cfg.get("escalation", {})
    sat_mm = float(esc_cfg.get("antecedent_rain_72h_mm", 100))
    now_dt = _ts(now)
    events: list[dict] = []
    levels_count: dict[str, int] = {"ACT": 0, "PREPARE": 0, "WATCH": 0, "QUIET": 0}
    ok_ids, failed_ids = [], []
    auto_runs: list[tuple[str, str, str]] = []
    auto_min = rank(get_settings().auto_plan_min_level.upper() or "PREPARE")

    for d in districts:
        try:
            with session_scope() as s:
                state_name = cfg.state_name(d.state)
                # ---------- river ----------
                river_pts = river.get(d.id, [])
                river_level = max_level(*[p.get("level") for p in river_pts]) if river_pts else None
                river_alerts = district_alerts(alerts, d, state_name, "river", now_dt) if alerts_ok else []
                r_color = strongest_color(river_alerts)
                if river_pts or r_color:
                    rres = resolve_level(river_level, official_color=r_color, floor_cfg=floor_cfg,
                                         official_available=alerts_ok, max_escalation=0)
                    ra = Assessment(
                        district_id=d.id, hazard="river", data_time=now_dt, model="glofas_v4",
                        level=rres.level, base_level=river_level, hysteresis_level=rres.level,
                        flags={**rres.as_dict(), "official_alerts": [a.as_dict() for a in river_alerts]},
                        details={"points": river_pts,
                                 "caveat": cfg.hazards["river"].get("caveat")},
                        fetch_ids=sorted({p["fetch_id"] for p in river_pts}),
                        model_run_id=_river_run_id(s, d.id, river_pts),
                    )
                    s.add(ra)
                    s.flush()
                    ev = apply_lifecycle(s, d.id, "river", ra, Observation(
                        level=rres.level, hysteresis_level=rres.level, model_run_id=ra.model_run_id,
                        window_start=now_dt, window_end=now_dt + dt.timedelta(days=7), can_open=True))
                    if ev:
                        events.append(ev)

                # ---------- rain ----------
                r = rain.get(d.id)
                rain_alerts = district_alerts(alerts, d, state_name, "rain", now_dt) if alerts_ok else []
                color = strongest_color(rain_alerts)
                ant = ante.get(d.id) or {}
                saturated = ant.get("mm_72h") is not None and ant["mm_72h"] >= sat_mm
                if r is None:
                    failed_ids.append(d.id)
                    if color is None:
                        continue  # nothing real to assess this scan; last assessment shows as STALE
                    rain_assess = None
                else:
                    ok_ids.append(d.id)
                    rain_assess = assess_rain(r["point"], r["model"], rain_cfg, now)
                base = rain_assess["base_level"] if rain_assess else None
                pre = max_level(base, floor_cfg.get(color) if color else None)
                compound = bool(esc_cfg.get("compound_with_river", True)) and rank(pre) >= 2 and rank(river_level) >= 1
                res = resolve_level(base, official_color=color, floor_cfg=floor_cfg, official_available=alerts_ok,
                                    saturated_ground=saturated, compound=compound,
                                    max_escalation=int(esc_cfg.get("max_levels", 1)))
                hres = resolve_level(rain_assess["hysteresis_level"] if rain_assess else None, official_color=color,
                                     floor_cfg=floor_cfg, official_available=alerts_ok, saturated_ground=saturated,
                                     compound=compound, max_escalation=int(esc_cfg.get("max_levels", 1)))

                # model-run identity
                run_id, run_time, sig = None, None, None
                if r is not None:
                    sig = run_signature(r["point"], now)
                    init = run_meta.get(r["model"])
                    if init:
                        run_time = _ts(init)
                        run_id = f"{r['model']}@{run_time.strftime('%Y-%m-%dT%H:%MZ')}"
                    else:
                        prev = _previous_assessment(s, d.id, "rain")
                        if prev is not None and prev.model == r["model"] and same_run(
                                (prev.details or {}).get("run_signature"), sig):
                            run_id = prev.model_run_id
                        else:
                            run_id = f"{r['model']}~{r['fetch_id']}"
                head = (rain_assess or {}).get("headline") or {}
                fired = (rain_assess or {}).get("rules_met") or []
                w_start, w_end = _rain_window(rain_assess, now, fired[0]["within_h"] if fired else None)
                if rain_assess is None and rain_alerts:
                    w_start = min((a.effective for a in rain_alerts if a.effective), default=now_dt)
                    w_end = max((a.expires for a in rain_alerts if a.expires), default=now_dt + dt.timedelta(days=2))
                flags = {
                    **res.as_dict(),
                    "saturated_ground": saturated,
                    "compound": compound,
                    "river_level": river_level,
                    "antecedent": ant or None,
                    "official_alerts": [a.as_dict() for a in rain_alerts],
                    "official_alerts_error": None if alerts_ok else alerts_state.get("error"),
                    "official_fetch_id": alerts_state.get("fetch_id"),
                    "ensemble_unavailable": r is None,
                    "fallback_from": (r or {}).get("fallback_from") or [],
                    "hysteresis": hres.as_dict(),
                }
                a = Assessment(
                    district_id=d.id, hazard="rain", data_time=now_dt,
                    model=(r or {}).get("model"), model_run_time=run_time, model_run_id=run_id,
                    n_members=(rain_assess or {}).get("n_members_expected"),
                    n_valid=(rain_assess or {}).get("n_valid_min"),
                    probabilities=(rain_assess or {}).get("probabilities") or {},
                    base_level=base, level=res.level, hysteresis_level=hres.level,
                    t_star=_ts(head.get("t_star")), t_minus_h=head.get("t_minus_h"),
                    onset_time=_ts(head.get("onset")), onset_h=head.get("onset_h"),
                    degraded=bool((rain_assess or {}).get("degraded")), stale=False,
                    insufficient=rain_assess is None,
                    flags=flags,
                    details={**({k: v for k, v in (rain_assess or {}).items() if k not in ("probabilities",)}),
                             "point_index": (r or {}).get("index"), "run_signature": sig},
                    fetch_ids=[x for x in [(r or {}).get("fetch_id"), ant.get("fetch_id"),
                                           alerts_state.get("fetch_id") if rain_alerts else None] if x],
                )
                s.add(a)
                s.flush()
                levels_count[res.level or "QUIET"] = levels_count.get(res.level or "QUIET", 0) + 1
                ev = apply_lifecycle(s, d.id, "rain", a, Observation(
                    level=res.level, hysteresis_level=hres.level, model_run_id=run_id,
                    window_start=w_start, window_end=w_end, can_open=True))
                if ev:
                    events.append(ev)
                if res.disagreement:
                    notify.publish("system", {"type": "sources_disagree", "district_id": d.id, "district": d.name,
                                              "notes": res.notes})
        except Exception:  # noqa: BLE001 - one bad district must not stop the scan
            log.exception("district %s failed", d.id)
            failed_ids.append(d.id)

    for ev in events:
        if ev["action"] in ("open", "upgrade", "reactivate") and rank(ev["level"]) >= 2:
            notify.publish("system", {"type": "level_change", **ev,
                                      "district": (get_config().district(ev["district_id"]) or d).name})
        if ev["action"] in ("open", "upgrade", "reactivate") and rank(ev["level"]) >= auto_min:
            auto_runs.append((ev["district_id"], ev["hazard"], ev["trigger_id"]))
    if auto_runs:
        from app.agent.runner import start_run_background

        for district_id, hazard, trigger_id in auto_runs:
            start_run_background(district_id, hazard, trigger_id=trigger_id, started_by="system (trigger)")

    return {
        "scan_id": scan_id,
        "districts_total": len(districts),
        "districts_ok": len(ok_ids),
        "districts_failed": sorted(set(failed_ids)),
        "levels": levels_count,
        "events": events,
        "errors": {
            "rain": {k: v for k, v in rain_err.items()},
            "antecedent": ante_err,
            "river": river_err,
            "sachet": None if alerts_ok else alerts_state.get("error"),
        },
        "models_used": sorted({v["model"] for v in rain.values()}),
    }


def _river_run_id(s, district_id: str, pts: list[dict]) -> str | None:
    """GloFAS run identity: the forecast series itself (re-scans of one run are identical)."""
    import hashlib
    import json

    if not pts:
        return None
    blob = json.dumps([p.get("chart", {}).get("river_discharge_median") for p in pts], sort_keys=True)
    return "glofas~" + hashlib.sha256(blob.encode()).hexdigest()[:12]

