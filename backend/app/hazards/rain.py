"""Rain plug-in: ensemble members -> probabilities, base level, t*, onset (spec §6.2–6.3).

Input: one :class:`EnsemblePoint` (all members for one district monitor point).
Output: a JSON-serialisable dict that the trigger engine and the agent consume. The
agent never sees raw member arrays — only these computed summaries (§6.7).
"""
from __future__ import annotations

from typing import Any

import numpy as np

from app.hazards.ensemble_math import exceedance, floor_hour, lead_time, rolling_accumulation
from app.sources.open_meteo import EnsemblePoint, EXPECTED_MEMBERS
from app.triggers.levels import evaluate_rain_rules, prob_key, required_windows


def assess_rain(pt: EnsemblePoint, model: str, cfg: dict, now: float) -> dict[str, Any]:
    window_h = int(cfg.get("accumulation_window_h", 24))
    cats: dict[str, float] = {k: float(v) for k, v in cfg["categories_mm"].items()}
    levels_cfg = cfg["levels"]
    acc = rolling_accumulation(pt.members, window_h)
    n_present = int(pt.members.shape[0])
    n_expected = int(EXPECTED_MEMBERS.get(model, n_present))

    probabilities: dict[str, dict] = {}
    for category, within_h in sorted(required_windows(levels_cfg)):
        ex = exceedance(acc, pt.times, cats[category], now, within_h)
        probabilities[prob_key(category, within_h)] = {"category": category, **ex.as_dict()}

    # Also report P for every category over the widest window (cards + agent context).
    widest = max(float(r["within_h"]) for lvl in levels_cfg.values() for r in lvl)
    for category, x in cats.items():
        k = prob_key(category, widest)
        if k not in probabilities:
            probabilities[k] = {"category": category, **exceedance(acc, pt.times, x, now, widest).as_dict()}

    p_only = {k: v["probability"] for k, v in probabilities.items()}
    base_level, met = evaluate_rain_rules(p_only, levels_cfg)
    margin = float(cfg.get("stand_down", {}).get("prob_margin", 0.15))
    hyst_level, _ = evaluate_rain_rules(p_only, levels_cfg, margin=margin)

    lead = {
        c: lead_time(acc, pt.times, x, now, widest, float(cfg.get("onset_member_share", 0.10))).as_dict()
        for c, x in cats.items()
    }
    # Headline category: the category of the strongest rule that fired, else the configured one.
    headline_cat = met[0]["category"] if met else cfg.get("headline_category", "very_heavy")
    head_lt = lead[headline_cat]
    head_rule_key = prob_key(met[0]["category"], met[0]["within_h"]) if met else prob_key(headline_cat, widest)
    head_p = probabilities[head_rule_key]

    n_valid_min = min((v["n_valid"] for v in probabilities.values()), default=0)
    min_share = float(cfg.get("min_valid_member_share", 0.8))
    degraded = n_valid_min < min_share * n_expected or n_present < min_share * n_expected

    return {
        "hazard": "rain",
        "model": model,
        "n_members_expected": n_expected,
        "n_members_present": n_present,
        "n_valid_min": n_valid_min,
        "degraded": bool(degraded),
        "now": int(now),
        "probabilities": probabilities,
        "base_level": base_level,
        "rules_met": met,
        "hysteresis_level": hyst_level,
        "lead": lead,
        "headline": {
            "category": headline_cat,
            "threshold_mm": cats[headline_cat],
            "rule_key": head_rule_key,
            "probability": head_p["probability"],
            "n_exceed": head_p["n_exceed"],
            "n_valid": head_p["n_valid"],
            "within_h": head_p["within_h"],
            "t_star": head_lt["t_star"],
            "t_minus_h": head_lt["t_minus_h"],
            "onset": head_lt["onset"],
            "onset_h": head_lt["onset_h"],
        },
        "grid": {"lat": pt.lat, "lon": pt.lon, "elevation": pt.elevation},
    }


def futures_series(pt: EnsemblePoint, cfg: dict, now: float, horizon_h: int = 168) -> dict[str, Any]:
    """Per-member 24-h accumulation series for the Futures chart (recomputed from raw)."""
    window_h = int(cfg.get("accumulation_window_h", 24))
    acc = rolling_accumulation(pt.members, window_h)
    start = floor_hour(now) - 12 * 3600
    sel = (pt.times >= start) & (pt.times <= floor_hour(now) + horizon_h * 3600)
    t = pt.times[sel]
    a = acc[:, sel]
    members = [[None if np.isnan(v) else round(float(v), 2) for v in row] for row in a]
    return {
        "times": [int(x) for x in t],
        "members": members,
        "member_names": pt.member_names,
        "categories_mm": cfg["categories_mm"],
        "now": int(now),
    }
