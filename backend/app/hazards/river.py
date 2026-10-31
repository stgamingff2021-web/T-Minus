"""River plug-in (GloFAS v4 via Open-Meteo Flood API) — spec §6.4.

* Baseline: each river point's own daily-discharge P95 and P99 from the GloFAS
  reanalysis (1984 -> mid-2022), fetched once and stored with provenance.
* Rules (config): WATCH  ensemble max    > P95 within 7 days
                  PREPARE ensemble median > P95 within 7 days
                  ACT    ensemble median > P99 within 5 days
* Caveat shown in the UI: GloFAS (~5 km) is weak on small urban channels; rainfall is
  the primary trigger and the river signal is supporting evidence.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from app.sources.open_meteo import FloodPoint
from app.triggers.levels import evaluate_river_rules

STAT_KEY = {"ensemble_max": "river_discharge_max", "ensemble_median": "river_discharge_median"}


def percentiles(values: np.ndarray) -> tuple[float, float, int]:
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if v.size < 365:
        raise ValueError(f"baseline too short ({v.size} valid days)")
    return float(np.percentile(v, 95)), float(np.percentile(v, 99)), int(v.size)


def assess_river_point(fp: FloodPoint, p95: float, p99: float, levels_cfg: dict, now: float) -> dict[str, Any]:
    day = 86400
    start = now - (now % day)
    exceed: dict[str, bool | None] = {}
    details: dict[str, Any] = {}
    for level, rule in levels_cfg.items():
        key = STAT_KEY.get(rule["statistic"])
        series = fp.series.get(key) if key else None
        thr = p95 if int(rule["above_percentile"]) == 95 else p99
        if series is None:
            exceed[level] = None
            continue
        sel = (fp.times >= start) & (fp.times < start + int(rule["within_days"]) * day)
        vals = series[sel]
        if vals.size == 0 or np.all(np.isnan(vals)):
            exceed[level] = None
            continue
        peak_idx = int(np.nanargmax(vals))
        peak = float(vals[peak_idx])
        exceed[level] = peak > thr
        first = np.where(vals > thr)[0]
        details[level] = {
            "statistic": rule["statistic"],
            "percentile": int(rule["above_percentile"]),
            "threshold_m3s": round(thr, 3),
            "peak_m3s": round(peak, 3),
            "peak_time": int(fp.times[sel][peak_idx]),
            "first_exceed_time": int(fp.times[sel][first[0]]) if first.size else None,
            "within_days": int(rule["within_days"]),
            "exceeds": bool(peak > thr),
        }
    level, _ = evaluate_river_rules(exceed, levels_cfg)
    return {"level": level, "rules": details, "p95": p95, "p99": p99}


def river_chart(fp: FloodPoint) -> dict[str, Any]:
    out = {"times": [int(t) for t in fp.times]}
    for k, v in fp.series.items():
        out[k] = [None if np.isnan(x) else round(float(x), 3) for x in v]
    return out
