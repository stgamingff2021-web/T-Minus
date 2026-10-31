"""Replay mode: backtesting without look-ahead bias (spec §6.10, AC-12).

Question answered: "how many hours of warning would T-Minus have given?" for a real
past event, using ONLY information that existed at the time.

Rule — forecasts as issued. We use the Open-Meteo Previous Runs API, never the
Historical Forecast API (which stitches together the first hours of each run — close
to what actually happened — and would leak the future into the past).

``precipitation_previous_dayN`` at valid time v comes from a model run initialised at
least N x 24 h before v. At replay clock time T, for each valid hour v we use
N = max(1, ceil((v - T) / 24 h)) so the run was always issued at or before T.

Probability in replay: archived ensemble members are not available, so we compute
MULTI-MODEL AGREEMENT — the share of archived global models whose 24-h total crosses
the threshold within the window — and label it as such. It is not identical to a live
51-member probability. Ground truth: observed rainfall from the reanalysis archive.

Isolation: results go to ``replay_runs`` only; replay never writes to the live triggers.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
from typing import Any

import numpy as np

from app.config import get_config
from app.db.models import ReplayRun, utcnow
from app.db.session import session_scope
from app.hazards.ensemble_math import HOUR, exceedance, rolling_accumulation
from app.sources.base import SourceError
from app.sources.open_meteo import fetch_archive_rain, fetch_previous_runs
from app.triggers.levels import LEVELS, evaluate_rain_rules, prob_key, required_windows

log = logging.getLogger("tminus.replay")
_tasks: dict[str, asyncio.Task] = {}


def _parse_t(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def as_issued_series(hourly: dict, valid_times: np.ndarray, as_of: int, max_day: int = 7) -> np.ndarray:
    """Pick, for each valid hour, the forecast issued at or before ``as_of`` (no look-ahead)."""
    times = np.asarray(hourly["time"], dtype=np.int64)
    index = {int(t): i for i, t in enumerate(times)}
    out = np.full(len(valid_times), np.nan)
    for j, v in enumerate(valid_times):
        lead_h = (int(v) - as_of) / HOUR
        n = max(1, math.ceil(lead_h / 24)) if lead_h > 0 else 1
        if n > max_day:
            continue
        col = hourly.get(f"precipitation_previous_day{n}")
        i = index.get(int(v))
        if col is None or i is None or col[i] is None:
            continue
        out[j] = float(col[i])
    return out


def agreement_at(model_hourly: dict[str, dict], as_of: int, rain_cfg: dict) -> dict[str, Any]:
    """Multi-model agreement for every rule window at replay time ``as_of``."""
    window_h = int(rain_cfg.get("accumulation_window_h", 24))
    cats = {k: float(v) for k, v in rain_cfg["categories_mm"].items()}
    windows = sorted(required_windows(rain_cfg["levels"]))
    horizon = max(w for _, w in windows)
    valid_times = np.arange(as_of - (window_h - 1) * HOUR, as_of + int(horizon) * HOUR + 1, HOUR, dtype=np.int64)
    rows, names = [], []
    for model, hourly in model_hourly.items():
        rows.append(as_issued_series(hourly, valid_times, as_of))
        names.append(model)
    if not rows:
        return {"probabilities": {}, "models": []}
    mat = np.vstack(rows)
    acc = rolling_accumulation(mat, window_h)
    probs = {}
    for cat, within_h in windows:
        ex = exceedance(acc, valid_times, cats[cat], as_of, within_h)
        probs[prob_key(cat, within_h)] = {"category": cat, **ex.as_dict()}
    per_model_max = {}
    for i, m in enumerate(names):
        sel = valid_times >= as_of
        r = acc[i, sel]
        per_model_max[m] = None if np.all(np.isnan(r)) else round(float(np.nanmax(r)), 1)
    return {"probabilities": probs, "models": names, "per_model_max_24h_mm": per_model_max}


def observed_peak(times: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    acc = rolling_accumulation(values.reshape(1, -1), 24)[0]
    if np.all(np.isnan(acc)):
        return {"peak_time": None, "peak_mm": None, "series": []}
    i = int(np.nanargmax(acc))
    series = [[int(t), None if np.isnan(a) else round(float(a), 1)] for t, a in zip(times[::3], acc[::3])]
    return {"peak_time": int(times[i]), "peak_mm": round(float(acc[i]), 1), "series": series}


async def _fetch_models(lat: float, lon: float, models: list[str], start: dt.datetime, end: dt.datetime,
                        fetch_ids: list[str], skipped: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in models:
        try:
            res, hourly_item = await fetch_previous_runs(lat, lon, m, start.date().isoformat(), end.date().isoformat())
        except SourceError as exc:
            skipped.append({"model": m, "reason": exc.message[:200]})
            continue
        fetch_ids.append(res.fetch_id)
        hourly = hourly_item.get("hourly") or {}
        cols = [k for k in hourly if k.startswith("precipitation_previous_day")]
        if not cols or all(all(v is None for v in hourly[c]) for c in cols):
            skipped.append({"model": m, "reason": "no archived as-issued data for this period"})
            continue
        out[m] = hourly
    return out


async def _timeline(lat, lon, models, start, end, step_h, rain_cfg, fetch_ids, skipped) -> dict[str, Any]:
    fetch_start = start - dt.timedelta(days=2)
    fetch_end = end + dt.timedelta(days=6)
    data = await _fetch_models(lat, lon, models, fetch_start, fetch_end, fetch_ids, skipped)
    steps = []
    first_fire: dict[str, int | None] = {lv: None for lv in LEVELS}
    t = int(start.timestamp())
    while t <= int(end.timestamp()):
        ag = agreement_at(data, t, rain_cfg)
        p_only = {k: v["probability"] for k, v in ag["probabilities"].items()}
        level, met = evaluate_rain_rules(p_only, rain_cfg["levels"])
        n_valid = min((v["n_valid"] for v in ag["probabilities"].values()), default=0)
        insufficient = n_valid < 3
        if insufficient:
            level, met = None, []
        for lv in LEVELS:
            if first_fire[lv] is None and level is not None and LEVELS.index(level) >= LEVELS.index(lv):
                first_fire[lv] = t
        steps.append({"as_of": t, "level": level, "rules_met": [m["category"] + f"@{int(m['within_h'])}h" for m in met],
                      "agreement": {k: {"p": v["probability"], "n_exceed": v["n_exceed"], "n_valid": v["n_valid"]}
                                    for k, v in ag["probabilities"].items()},
                      "per_model_max_24h_mm": ag.get("per_model_max_24h_mm"), "insufficient": insufficient})
        t += step_h * HOUR
    return {"steps": steps, "first_fire": first_fire, "models_used": list(data)}


async def execute(replay_id: str, event_key: str) -> None:
    cfg = get_config()
    ev = next((e for e in cfg.replay.get("events", []) if e["key"] == event_key), None)
    try:
        if ev is None:
            raise ValueError(f"unknown replay event {event_key!r}")
        d = cfg.district(ev["district"])
        lat, lon = d.hq
        models = cfg.replay.get("models", [])
        rain_cfg = cfg.hazards["rain"]
        fetch_ids: list[str] = []
        skipped: list[dict] = []
        start, end, step_h = _parse_t(ev["as_of_start"]), _parse_t(ev["as_of_end"]), int(ev.get("step_h", 6))
        main = await _timeline(lat, lon, models, start, end, step_h, rain_cfg, fetch_ids, skipped)
        if not main["models_used"]:
            reasons = "; ".join(sorted({m["reason"][:120] for m in skipped})) or "no archived forecasts"
            raise ValueError(f"No as-issued forecasts could be fetched for this event ({reasons}). Nothing is estimated.")
        try:
            truth_res, truth = await fetch_archive_rain(lat, lon, _parse_t(ev["truth_start"]).date().isoformat(),
                                                        _parse_t(ev["truth_end"]).date().isoformat())
            fetch_ids.append(truth_res.fetch_id)
            peak = observed_peak(truth.times, truth.values)
        except SourceError as exc:  # ground truth unavailable: report fire times, no lead time (never invented)
            peak = {"peak_time": None, "peak_mm": None, "series": [], "error": exc.message[:200]}
        leads = {lv: (None if (ft is None or peak["peak_time"] is None) else round((peak["peak_time"] - ft) / HOUR, 1))
                 for lv, ft in main["first_fire"].items()}
        quiet_skipped: list[dict] = []
        q_start, q_end = _parse_t(ev["quiet_start"]), _parse_t(ev["quiet_end"])
        quiet = await _timeline(lat, lon, models, q_start, q_end, step_h, rain_cfg, fetch_ids, quiet_skipped)
        try:
            q_truth_res, q_truth = await fetch_archive_rain(lat, lon, q_start.date().isoformat(), q_end.date().isoformat())
            fetch_ids.append(q_truth_res.fetch_id)
            q_peak = observed_peak(q_truth.times, q_truth.values)
        except SourceError as exc:
            q_peak = {"peak_time": None, "peak_mm": None, "series": [], "error": exc.message[:200]}
        false_alarms = {lv: ft is not None for lv, ft in quiet["first_fire"].items()}
        vh = float(rain_cfg["categories_mm"]["very_heavy"])
        result = {
            "event": {k: ev.get(k) for k in ("key", "name", "district", "as_of_start", "as_of_end", "truth_start",
                                             "truth_end", "quiet_start", "quiet_end", "references")},
            "district": {"id": d.id, "name": d.name, "lat": lat, "lon": lon},
            "method": "multi-model agreement (share of archived models exceeding the threshold), forecasts as issued "
                      "(Open-Meteo Previous Runs API, previous_dayN chosen so every run predates the replay clock)",
            "label": "REPLAY · real forecasts as issued at the time",
            "models_requested": models, "models_used": main["models_used"], "models_skipped": skipped,
            "timeline": main["steps"], "first_fire": main["first_fire"],
            "observed": peak, "lead_time_h": leads,
            "target_met": leads.get("PREPARE") is not None and leads["PREPARE"] >= 24,
            "quiet_period": {"timeline": quiet["steps"], "first_fire": quiet["first_fire"], "false_alarm": false_alarms,
                             "observed": q_peak, "models_used": quiet["models_used"], "models_skipped": quiet_skipped,
                             "observed_exceeded_very_heavy": (q_peak.get("peak_mm") or 0) >= vh},
        }
        status, err = "completed", None
    except (SourceError, ValueError) as exc:
        result, status, err = None, "failed", getattr(exc, "message", str(exc))
        fetch_ids = locals().get("fetch_ids", [])
    except Exception as exc:  # noqa: BLE001
        log.exception("replay failed")
        result, status, err = None, "failed", f"{type(exc).__name__}: {exc}"
        fetch_ids = locals().get("fetch_ids", [])
    with session_scope() as s:
        r = s.get(ReplayRun, replay_id)
        r.status, r.result, r.error, r.finished_at, r.fetch_ids = status, result, err, utcnow(), fetch_ids


def start(event_key: str) -> str:
    with session_scope() as s:
        r = ReplayRun(event_key=event_key)
        s.add(r)
        s.flush()
        rid = r.id
    _tasks[rid] = asyncio.get_running_loop().create_task(execute(rid, event_key))
    return rid
