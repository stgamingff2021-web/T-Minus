"""Feature 4 (+ a light feature 5) — critical assets at risk, and where stock could go.

Assets: OpenStreetMap (food/grain godowns and warehouses, cold storage, seed stores,
hospitals, substations, water works, fuel stations; cached 24 h) plus assets named in the
district plan (feature 1; listed with their page, not mapped).

Risk per mapped asset, from the impact map (feature 2):
* physical — impact class of the asset's cell and whether the asset sits on low ground;
* connectivity — share of the neighbouring cells in the high/medium impact class (an asset
  surrounded by flooding is hard to reach even if it stays dry);
* operational — time to expected impact.
Measures come from ``config/hazards.yaml`` → ``assets.measures`` and are labelled
"Suggested — not in district plan". For storage at risk, meaningfully safer storage sites
(lower impact, not high risk) are listed safest-first with straight-line distance; travel time
needs a route and is marked unchecked.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.config import get_config
from app.db.models import FacilityCache, utcnow
from app.db.session import session_scope
from app.geo import load_boundaries
from app.impact.engine import elevations, get_impact, haversine_km, pkey
from app.plandb.store import district_records
from app.sources.base import SourceError
from app.sources.osm import fetch_assets

TYPES_KEY = "assets:v1"
LABELS = {"grain_storage": "Food / grain storage", "cold_storage": "Cold storage", "seed_store": "Seed store",
          "hospital": "Hospital", "substation": "Substation", "water_works": "Water works", "fuel": "Fuel station",
          "ration_shop": "Ration shop"}
RANK = {"high": 0, "medium": 1, "low": 2}


def _cfg() -> dict:
    return get_config().hazards.get("assets", {})


def _bbox(district_id: str) -> tuple[float, float, float, float]:
    from shapely.geometry import shape

    geometry = load_boundaries()[0].get(district_id)
    if geometry:
        minx, miny, maxx, maxy = shape(geometry).bounds
        return miny, minx, maxy, maxx
    d = get_config().district(district_id)
    r = (d.search_radius_km or 15.0) / 111.32
    return d.hq[0] - r, d.hq[1] - r, d.hq[0] + r, d.hq[1] + r


async def osm_assets(district_id: str, force: bool = False) -> tuple[list[dict], str | None, str | None]:
    ttl = dt.timedelta(hours=float(_cfg().get("cache_h", 24)))
    with session_scope() as s:
        row = s.execute(select(FacilityCache).where(FacilityCache.district_id == district_id,
                                                    FacilityCache.types_key == TYPES_KEY)
                        .order_by(FacilityCache.fetched_at.desc()).limit(1)).scalars().first()
        if row and not force and utcnow() - row.fetched_at < ttl:
            return list(row.features), row.fetch_id, None
        stale = (list(row.features), row.fetch_id) if row else None
    try:
        res, feats = await fetch_assets(_bbox(district_id))
    except SourceError as exc:
        if stale:
            return stale[0], stale[1], f"OpenStreetMap unreachable; showing the last list ({exc})"[:240]
        return [], None, f"OpenStreetMap unreachable: {exc}"[:240]
    with session_scope() as s:
        s.add(FacilityCache(district_id=district_id, types_key=TYPES_KEY, fetch_id=res.fetch_id, features=feats))
    return feats, res.fetch_id, None


def _nearest(cells: list[dict], lat: float, lon: float) -> dict | None:
    return min(cells, key=lambda c: haversine_km((c["lat"], c["lon"]), (lat, lon))) if cells else None


def assess(asset: dict, cells: list[dict], spacing_km: float, t_minus_h: float | None, low_cut: float | None) -> dict:
    cell = _nearest(cells, asset["lat"], asset["lon"])
    reasons, physical = [], "low"
    if cell:
        if cell["class"] == "high":
            physical = "high"
        elif cell["class"] == "medium":
            physical = "medium"
        reasons += [f"impact {cell['class']}"] + cell.get("factors", [])[:2]
    elev = asset.get("elevation_m")
    low_ground = elev is not None and low_cut is not None and elev <= low_cut
    if low_ground:
        reasons.append("on low ground")
        if physical == "medium":
            physical = "high"
    neigh = [c for c in cells if c is not cell and haversine_km((c["lat"], c["lon"]), (asset["lat"], asset["lon"])) <= 1.6 * spacing_km]
    cut = sum(c["class"] == "high" for c in neigh)
    connectivity = "high" if neigh and cut / len(neigh) >= 0.75 else "medium" if neigh and cut / len(neigh) >= 0.4 else "low"
    if connectivity != "low":
        reasons.append(f"access cells affected {cut}/{len(neigh)}")
    risk = min(physical, connectivity, key=lambda r: RANK[r])
    urgent = risk != "low" and t_minus_h is not None and t_minus_h <= 24
    return {**asset, "label": LABELS.get(asset["type"], asset["type"]), "risk": risk, "physical": physical,
            "connectivity": connectivity, "urgent": urgent, "reasons": reasons,
            "cell_class": cell["class"] if cell else None, "cell_impact": cell["impact"] if cell else None,
            "measures": (_cfg().get("measures") or {}).get(asset["type"], []) if risk != "low" else []}


async def get_assets(district_id: str, force: bool = False) -> dict:
    impact = await get_impact(district_id)
    cells, spacing = impact.get("cells") or [], impact.get("spacing_km") or 5.0
    feats, fetch_id, osm_error = await osm_assets(district_id, force)
    feats = feats[:300]
    elev, _, elev_error = await elevations([(f["lat"], f["lon"]) for f in feats]) if feats else ({}, [], None)
    values = sorted(c["elevation_m"] for c in cells)
    low_cut = values[max(0, len(values) // 5 - 1)] if values else None  # lowest fifth of the district
    t_minus = (impact.get("hazard") or {}).get("t_minus_h")
    assets = [assess({**f, "elevation_m": elev.get(pkey(f["lat"], f["lon"]))}, cells, spacing, t_minus, low_cut)
              for f in feats]
    reloc_types = set(_cfg().get("relocation_types") or [])
    stores = [a for a in assets if a["type"] in reloc_types and a["cell_impact"] is not None]
    for a in assets:
        if a["type"] in reloc_types and a["risk"] != "low" and a["cell_impact"] is not None:
            # meaningfully safer sites (not themselves high risk), safest first, then nearest
            safer = [o for o in stores if o is not a and o["risk"] != "high" and o["cell_impact"] <= a["cell_impact"] - 0.05]
            options = sorted(safer, key=lambda o: (o["cell_impact"], haversine_km((a["lat"], a["lon"]), (o["lat"], o["lon"]))))[:3]
            a["relocation"] = [{"id": o["id"], "name": o["name"] or o["label"], "type": o["type"],
                                "distance_km": round(haversine_km((a["lat"], a["lon"]), (o["lat"], o["lon"])), 1),
                                "elevation_m": o.get("elevation_m"), "impact_class": o["cell_class"],
                                "route": "not checked"} for o in options]
    assets.sort(key=lambda a: (RANK[a["risk"]], not a["urgent"], a["label"], a["name"] or ""))
    plan_assets = [{"label": r["label"], "type": r["fields"].get("asset_type"), "page": r["page"],
                    "document_id": r["document_id"], "quote": r["quote"]}
                   for r in district_records(district_id, ("asset",))]
    counts = {k: sum(a["risk"] == k for a in assets) for k in ("high", "medium", "low")}
    return {"district_id": district_id, "computed_at": utcnow().isoformat(), "impact_id": impact.get("id"),
            "hazard_index": impact.get("hazard_index"), "t_minus_h": t_minus, "assets": assets,
            "plan_assets": plan_assets[:200], "counts": counts, "osm_fetch_id": fetch_id,
            "osm_error": osm_error, "terrain_error": elev_error or impact.get("terrain_error")}
