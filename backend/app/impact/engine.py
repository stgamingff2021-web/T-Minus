"""Feature 2 — where in the district is the hazard likely to hit hardest?

Hazard (district-wide, from the live assessments): probability of the trigger category from
the ensemble, the intensity profile (probability of heavy / very heavy / extremely heavy rain),
the ensemble probability of cyclonic-storm-force wind (a separate request, so a failure never
touches the scanner) and soil saturation (72-h antecedent rain). The ensemble grid is ~25 km,
so the hazard is uniform across a district; local differences come from terrain.

Susceptibility per grid cell (weights in ``config/hazards.yaml`` → ``impact``): low ground
within the district (Copernicus DEM via Open-Meteo), a depression lower than the surrounding
cells, and distance to the district's river points.

    impact = hazard index x (floor + (1 - floor) x susceptibility)  ->  high / medium / low / minimal

The most affected cells are named with OpenStreetMap (Nominatim reverse) and their settlement
type is used as exposure. Every input is a real value with its fetch id; when terrain cannot
be fetched no cells are shown.
"""
from __future__ import annotations

import asyncio
import math
import statistics
import time

import numpy as np
from shapely.geometry import Point, shape
from sqlalchemy import select

from app.config import get_config
from app.db.models import Assessment, ImpactAssessment, PlaceName, TerrainPoint, utcnow
from app.db.session import session_scope
from app.geo import load_boundaries
from app.sources.base import SourceError
from app.sources.open_meteo import fetch_ensemble_multi
from app.sources.osm import reverse
from app.sources.terrain import MAX_POINTS, fetch_elevation

URBAN = ("city", "town", "suburb", "neighbourhood", "quarter", "city_district", "borough")
SPARSE = ("hamlet", "isolated_dwelling", "farm", "locality")
_locks: dict[str, asyncio.Lock] = {}


def _cfg() -> dict:
    return get_config().hazards.get("impact", {})


def pkey(lat: float, lon: float) -> str:
    return f"{lat:.4f},{lon:.4f}"


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def grid(geometry: dict | None, hq: tuple[float, float], radius_km: float, max_cells: int) -> tuple[list[tuple[float, float]], float]:
    """Evenly spaced points inside the district (or a circle round the HQ when no boundary)."""
    if geometry:
        poly = shape(geometry)
        minx, miny, maxx, maxy = poly.bounds
        lat0 = (miny + maxy) / 2
        area_km2 = poly.area * 111.32 ** 2 * math.cos(math.radians(lat0))
        inside = poly.contains
    else:
        lat0 = hq[0]
        dlat, dlon = radius_km / 111.32, radius_km / (111.32 * math.cos(math.radians(lat0)))
        minx, miny, maxx, maxy = hq[1] - dlon, hq[0] - dlat, hq[1] + dlon, hq[0] + dlat
        area_km2 = math.pi * radius_km ** 2
        inside = lambda p: haversine_km(hq, (p.y, p.x)) <= radius_km  # noqa: E731
    spacing = max(0.8, math.sqrt(area_km2 / max(1, max_cells)))
    pts: list[tuple[float, float]] = []
    for _ in range(5):
        dlat = spacing / 111.32
        dlon = spacing / (111.32 * math.cos(math.radians(lat0)))
        pts = []
        lat = miny + dlat / 2
        while lat < maxy:
            lon = minx + dlon / 2
            while lon < maxx:
                if inside(Point(lon, lat)):
                    pts.append((round(lat, 4), round(lon, 4)))
                lon += dlon
            lat += dlat
        if 0.6 * max_cells <= len(pts) <= 1.2 * max_cells:
            break
        spacing *= math.sqrt(len(pts) / max_cells) if pts else 0.6
    if not pts:
        pts = [(round(hq[0], 4), round(hq[1], 4))]
    return pts[: int(max_cells * 1.2)], round(spacing, 2)


async def elevations(points: list[tuple[float, float]]) -> tuple[dict[str, float | None], list[str], str | None]:
    keys = [pkey(*p) for p in points]
    with session_scope() as s:
        have = {t.key for t in s.execute(select(TerrainPoint).where(TerrainPoint.key.in_(keys))).scalars()}
    missing = [p for p, k in zip(points, keys) if k not in have]
    fetch_ids, error = [], None
    for i in range(0, len(missing), MAX_POINTS):
        chunk = missing[i:i + MAX_POINTS]
        try:
            res, vals = await fetch_elevation(chunk)
        except SourceError as exc:
            error = str(exc)[:200]
            break
        fetch_ids.append(res.fetch_id)
        with session_scope() as s:
            for (lat, lon), v in zip(chunk, vals):
                s.merge(TerrainPoint(key=pkey(lat, lon), lat=lat, lon=lon, elevation_m=v, fetch_id=res.fetch_id))
    with session_scope() as s:
        out = {t.key: t.elevation_m for t in s.execute(select(TerrainPoint).where(TerrainPoint.key.in_(keys))).scalars()}
        if not fetch_ids:
            fetch_ids = sorted({t.fetch_id for t in s.execute(select(TerrainPoint).where(TerrainPoint.key.in_(keys))).scalars() if t.fetch_id})[:5]
    return out, fetch_ids, error


def _place_dict(p: PlaceName) -> dict:
    return {"name": p.name, "display_name": p.display_name, "settlement": p.place_type, "population": p.population,
            "osm_id": p.osm_id, "fetch_id": p.fetch_id}


def settlement_of(address: dict) -> str:
    if any(k in address for k in URBAN):
        return "urban"
    if "village" in address:
        return "village"
    if any(k in address for k in SPARSE):
        return "sparse"
    return "unknown"


async def place_names(points: list[tuple[float, float]]) -> dict[str, dict]:
    keys = [pkey(*p) for p in points]
    with session_scope() as s:
        cached = {p.key: _place_dict(p) for p in s.execute(select(PlaceName).where(PlaceName.key.in_(keys))).scalars()}
    for (lat, lon), k in zip(points, keys):
        if k in cached:
            continue
        try:
            res, p = await reverse(lat, lon)
        except SourceError:
            continue
        addr = (p or {}).get("address") or {}
        local = next((addr[x] for x in ("suburb", "neighbourhood", "quarter", "village", "hamlet", "city_district",
                                         "town", "locality") if addr.get(x)), None) or (p or {}).get("name")
        town = next((addr[x] for x in ("city", "town", "county") if addr.get(x)), None)
        name = local if not town or town == local else f"{local}, {town}"
        row = PlaceName(key=k, name=name, display_name=(p or {}).get("display_name"), place_type=settlement_of(addr),
                        place_rank=(p or {}).get("place_rank"), population=((p or {}).get("extratags") or {}).get("population"),
                        osm_id=f"osm:{p.get('osm_type')}/{p.get('osm_id')}" if p and p.get("osm_id") else None,
                        fetch_id=res.fetch_id)
        with session_scope() as s:
            s.merge(row)
        cached[k] = _place_dict(row)
    return cached


def _latest(s, district_id: str, hazard: str) -> Assessment | None:
    return s.execute(select(Assessment).where(Assessment.district_id == district_id, Assessment.hazard == hazard)
                     .order_by(Assessment.computed_at.desc()).limit(1)).scalars().first()


def hazard_summary(district_id: str) -> dict:
    from app.agent.tools import assessment_card

    with session_scope() as s:
        rain = _latest(s, district_id, "rain")
        card = assessment_card(rain) if rain else None
    if card is None:
        return {"available": False}
    head = card["headline"]
    order = {"heavy": 0, "very_heavy": 1, "extremely_heavy": 2}
    intensity = sorted(
        [{"category": v["category"], "threshold_mm": v["threshold_mm"], "within_h": v["within_h"],
          "probability_pct": v["probability_pct"], "members": v["members_exceeding"], "valid": v["valid_members"]}
         for v in card["probabilities"].values() if v.get("probability_pct") is not None],
        key=lambda r: (order.get(r["category"], 9), r["within_h"] or 0))
    return {"available": True, "level": card["level"], "model": card["model"], "model_run": card["model_run"],
            "data_time": card["data_time"], "category": head["category"], "threshold_mm": head["threshold_mm"],
            "probability_pct": head["probability_pct"], "members": head["members_exceeding"], "valid": head["valid_members"],
            "within_h": head["within_h"], "expected_impact_utc": head["expected_impact_utc"], "t_minus_h": head["t_minus_h"],
            "antecedent_72h_mm": card["antecedent_rain_72h_mm"], "saturated": bool(card["saturated_ground"]),
            "river_level": card["river_level"], "intensity": intensity, "assessment_id": card["assessment_id"]}


async def wind_hazard(hq: tuple[float, float]) -> dict:
    cfg = _cfg()
    model = cfg.get("wind_model", "ecmwf_ifs025")
    thr = cfg.get("wind_kmh") or {"cyclonic": 62, "severe": 89}
    try:
        res, pts = await fetch_ensemble_multi([hq], model, ["wind_speed_10m"], forecast_days=3, source="wind")
    except (SourceError, ValueError, KeyError) as exc:
        return {"available": False, "error": str(exc)[:200]}
    p = pts[0]["wind_speed_10m"]
    now = time.time()
    mask = (p.times >= now - 3600) & (p.times <= now + 72 * 3600)
    m = p.members[:, mask] if mask.any() else p.members
    valid = ~np.all(np.isnan(m), axis=1)
    n = int(valid.sum())
    if n == 0:
        return {"available": False, "error": "no wind values in the forecast"}
    peak = np.nanmax(m[valid], axis=1)
    n_cyc, n_sev = int((peak >= thr["cyclonic"]).sum()), int((peak >= thr["severe"]).sum())
    return {"available": True, "model": model, "members": n, "horizon_h": 72, "fetch_id": res.fetch_id,
            "cyclonic_kmh": thr["cyclonic"], "severe_kmh": thr["severe"],
            "n_cyclonic": n_cyc, "p_cyclonic": round(n_cyc / n, 3), "n_severe": n_sev, "p_severe": round(n_sev / n, 3),
            "peak_kmh_median": round(float(np.median(peak)), 1), "peak_kmh_max": round(float(peak.max()), 1)}


def classify(impact: float) -> str:
    c = _cfg().get("classes") or {"high": 0.5, "medium": 0.25, "low": 0.1}
    return "high" if impact >= c["high"] else "medium" if impact >= c["medium"] else "low" if impact >= c["low"] else "minimal"


def score_cells(points: list[tuple[float, float]], elev: dict[str, float | None], rivers: list[tuple[str, float, float]],
                spacing_km: float, hazard_index: float) -> list[dict]:
    cfg = _cfg()
    w = cfg.get("weights") or {"low_elevation": 0.45, "depression": 0.25, "river_proximity": 0.30}
    dscale, rscale = float(cfg.get("depression_scale_m", 5)), float(cfg.get("river_scale_km", 5))
    floor = float(cfg.get("susceptibility_floor", 0.4))
    cells = [{"lat": la, "lon": lo, "elevation_m": elev.get(pkey(la, lo))} for la, lo in points]
    cells = [c for c in cells if c["elevation_m"] is not None]
    if not cells:
        return []
    values = sorted(c["elevation_m"] for c in cells)
    median = statistics.median(values)
    n = len(values)
    for c in cells:
        e = c["elevation_m"]
        rank = sum(v < e for v in values) / max(1, n - 1)
        neigh = [o["elevation_m"] for o in cells if o is not c and haversine_km((c["lat"], c["lon"]), (o["lat"], o["lon"])) <= 1.6 * spacing_km]
        comps, factors = {"low_elevation": 1 - rank}, []
        if rank <= 0.1:
            factors.append("lowest 10% of the district")
        if neigh:
            local = e - sum(neigh) / len(neigh)
            comps["depression"] = min(1.0, max(0.0, -local / dscale))
            c["local_m"] = round(local, 1)
            if local <= -1:
                factors.append(f"{abs(local):.0f} m below surrounding cells")
        if rivers:
            name, km = min(((r[0], haversine_km((c["lat"], c["lon"]), (r[1], r[2]))) for r in rivers), key=lambda t: t[1])
            comps["river_proximity"] = min(1.0, max(0.0, 1 - km / rscale))
            c["river_km"], c["river"] = round(km, 1), name
            if km <= rscale:
                factors.append(f"{km:.1f} km from {name}")
        wsum = sum(w[k] for k in comps)
        s = round(sum(w[k] * v for k, v in comps.items()) / wsum if wsum else 0.0, 3)  # shown value drives impact
        c["rel_m"] = round(e - median, 1)
        c["susceptibility"] = s
        c["impact"] = round(hazard_index * (floor + (1 - floor) * s), 3)
        c["class"] = classify(c["impact"])
        c["factors"] = factors
    return cells


def hazard_index(h: dict, wind: dict) -> tuple[float, list[str]]:
    parts, why = [], []
    if h.get("available") and h.get("probability_pct") is not None:
        parts.append(h["probability_pct"] / 100)
        why.append(f"rain {h['probability_pct']:g}%")
    if wind.get("available"):
        parts.append(wind["p_cyclonic"])
        why.append(f"wind {wind['p_cyclonic'] * 100:.0f}%")
    idx = max(parts) if parts else 0.0
    if h.get("saturated"):
        idx = min(1.0, idx * float(_cfg().get("saturation_multiplier", 1.2)))
        why.append("saturated ground")
    return round(idx, 3), why


async def compute(district_id: str) -> dict:
    cfg = _cfg()
    d = get_config().district(district_id)
    geometry = load_boundaries()[0].get(district_id)
    points, spacing = grid(geometry, d.hq, d.search_radius_km or 15.0, int(cfg.get("max_cells", 64)))
    h = hazard_summary(district_id)
    wind = await wind_hazard(d.hq)
    elev, fetch_ids, elev_error = await elevations(points)
    idx, why = hazard_index(h, wind)
    cells = score_cells(points, elev, [(r.name, r.lat, r.lon) for r in d.rivers], spacing, idx)
    ew = cfg.get("exposure_weights") or {}
    top = sorted(cells, key=lambda c: (-c["impact"], -c["susceptibility"]))[: int(cfg.get("name_top_cells", 8))]
    names = await place_names([(c["lat"], c["lon"]) for c in top]) if top else {}
    areas, seen = [], set()
    for c in top:
        place = names.get(pkey(c["lat"], c["lon"]))
        if place:
            c["place"] = place
        label = (place or {}).get("name") or f"{c['lat']:.3f}, {c['lon']:.3f}"
        if label in seen:
            continue
        seen.add(label)
        settlement = (place or {}).get("settlement") or "unknown"
        areas.append({"name": label, "lat": c["lat"], "lon": c["lon"], "class": c["class"], "impact": c["impact"],
                      "susceptibility": c["susceptibility"], "elevation_m": c["elevation_m"], "factors": c["factors"],
                      "settlement": settlement, "population": (place or {}).get("population"),
                      "priority": round(c["impact"] * float(ew.get(settlement, ew.get("unknown", 0.8))), 3)})
    areas.sort(key=lambda a: (-a["priority"], -a["susceptibility"]))
    counts = {k: sum(c["class"] == k for c in cells) for k in ("high", "medium", "low", "minimal")}
    data = {"district_id": district_id, "computed_at": utcnow().isoformat(), "hazard": h, "wind": wind,
            "hazard_index": idx, "hazard_drivers": why, "spacing_km": spacing, "cells": cells, "areas": areas,
            "counts": counts, "terrain_error": elev_error if not cells else None,
            "boundary": "district boundary" if geometry else "radius around HQ"}
    ids = fetch_ids + ([wind["fetch_id"]] if wind.get("fetch_id") else [])
    with session_scope() as s:
        row = ImpactAssessment(district_id=district_id, model_run_id=h.get("model_run"), data=data, fetch_ids=ids)
        s.add(row)
        s.flush()
        data["id"] = row.id
    return data


async def get_impact(district_id: str, force: bool = False) -> dict:
    refresh = float(_cfg().get("refresh_min", 60)) * 60
    run = hazard_summary(district_id).get("model_run")

    def cached() -> dict | None:
        with session_scope() as s:
            row = s.execute(select(ImpactAssessment).where(ImpactAssessment.district_id == district_id)
                            .order_by(ImpactAssessment.computed_at.desc()).limit(1)).scalars().first()
            if row and (utcnow() - row.computed_at).total_seconds() < refresh and row.model_run_id == run and row.data.get("cells"):
                return {**row.data, "id": row.id}
        return None

    if not force and (hit := cached()):
        return hit
    lock = _locks.setdefault(district_id, asyncio.Lock())
    async with lock:
        if not force and (hit := cached()):
            return hit
        return await compute(district_id)
