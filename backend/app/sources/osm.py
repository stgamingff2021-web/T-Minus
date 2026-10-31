"""OpenStreetMap clients: Overpass (facilities) and Nominatim (geocoding). © OpenStreetMap contributors.

* Overpass — hospitals, schools, shelters, community halls, fire and police stations
  inside a district (boundary bbox when loaded, otherwise a radius around the HQ).
  Cached per district for 24 h (§9.5) by the caller.
* Nominatim — geocodes place names found in plans, bounded to the district's box.
  Usage policy: max 1 request/s, custom User-Agent, every result cached permanently.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config import get_settings
from app.sources.base import FetchResult, SourceError, fetch

FACILITY_TYPES = ("hospital", "school", "shelter", "community_centre", "fire_station", "police", "clinic")

_OVERPASS_FILTERS = {
    "hospital": ['nwr["amenity"="hospital"]', 'nwr["healthcare"="hospital"]'],
    "clinic": ['nwr["amenity"="clinic"]', 'nwr["healthcare"="centre"]'],
    "school": ['nwr["amenity"="school"]', 'nwr["amenity"="college"]'],
    "shelter": [
        'nwr["amenity"="shelter"]["shelter_type"!~"public_transport|picnic_shelter|lean_to|basic_hut|field_shelter"]',
        'nwr["emergency"="assembly_point"]',
        'nwr["social_facility"="shelter"]',
    ],
    "community_centre": ['nwr["amenity"="community_centre"]', 'nwr["amenity"="events_venue"]'],
    "fire_station": ['nwr["amenity"="fire_station"]'],
    "police": ['nwr["amenity"="police"]'],
}


def build_overpass_query(types: list[str], *, bbox: tuple[float, float, float, float] | None = None,
                         around: tuple[float, float, float] | None = None, timeout_s: int = 60) -> str:
    """bbox = (south, west, north, east); around = (radius_m, lat, lon)."""
    if bbox:
        scope = f"({bbox[0]:.5f},{bbox[1]:.5f},{bbox[2]:.5f},{bbox[3]:.5f})"
    elif around:
        scope = f"(around:{int(around[0])},{around[1]:.5f},{around[2]:.5f})"
    else:
        raise ValueError("bbox or around required")
    parts = []
    for t in types:
        for f in _OVERPASS_FILTERS.get(t, []):
            parts.append(f + scope + ";")
    return f"[out:json][timeout:{timeout_s}];(" + "".join(parts) + ");out center tags 2000;"


def classify(tags: dict) -> str | None:
    a = tags.get("amenity")
    if a in ("hospital",) or tags.get("healthcare") == "hospital":
        return "hospital"
    if a == "clinic" or tags.get("healthcare") == "centre":
        return "clinic"
    if a in ("school", "college"):
        return "school"
    if a == "shelter" or tags.get("emergency") == "assembly_point" or tags.get("social_facility") == "shelter":
        return "shelter"
    if a in ("community_centre", "events_venue"):
        return "community_centre"
    if a == "fire_station":
        return "fire_station"
    if a == "police":
        return "police"
    return None


def normalise_elements(payload: dict) -> list[dict]:
    out = []
    seen = set()
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        kind = classify(tags)
        if not kind:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        fid = f"osm:{el.get('type')}/{el.get('id')}"
        if fid in seen:
            continue
        seen.add(fid)
        out.append({
            "id": fid,
            "name": tags.get("name") or tags.get("name:en"),
            "name_ta": tags.get("name:ta"),
            "type": kind,
            "lat": float(lat),
            "lon": float(lon),
            "tags": {k: tags[k] for k in ("amenity", "healthcare", "emergency", "operator", "addr:full",
                                          "addr:street", "beds", "capacity", "shelter_type") if k in tags},
        })
    return out


def _validate_overpass(p: Any) -> None:
    if not isinstance(p, dict) or "elements" not in p:
        raise ValueError("missing 'elements'")


async def fetch_facilities(types: list[str], *, bbox=None, around=None) -> tuple[FetchResult, list[dict]]:
    """Query Overpass, failing over across public mirrors (each attempt is logged)."""
    s = get_settings()
    q = build_overpass_query(types, bbox=bbox, around=around)
    urls = [s.overpass_url] + [u.strip() for u in s.overpass_mirrors.split(",") if u.strip()]
    last: SourceError | None = None
    deadline = time.monotonic() + 40  # total budget across mirrors
    for url in urls:
        left = deadline - time.monotonic()
        if left < 5:
            break
        try:
            res = await fetch("overpass", url, method="POST", data={"data": q}, validate=_validate_overpass,
                              timeout_s=min(20.0, left), attempts=1, log_params={"query": q, "endpoint": url})
            return res, normalise_elements(res.payload)
        except SourceError as exc:
            last = exc
            if exc.simulated:
                break
    raise last or SourceError("overpass", "no Overpass endpoint configured")


# ---------------------------------------------------------------------------
# Nominatim
# ---------------------------------------------------------------------------

_nominatim_lock = asyncio.Lock()
_last_call = 0.0


def _validate_nominatim(p: Any) -> None:
    if not isinstance(p, list):
        raise ValueError("expected a JSON list")


async def geocode(query: str, viewbox: tuple[float, float, float, float]) -> tuple[FetchResult, dict | None]:
    """viewbox = (west, south, east, north). Returns (fetch, best match or None)."""
    global _last_call
    s = get_settings()
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "in",
        "viewbox": f"{viewbox[0]:.5f},{viewbox[3]:.5f},{viewbox[2]:.5f},{viewbox[1]:.5f}",
        "bounded": 1,
    }
    if s.contact_email:
        params["email"] = s.contact_email
    async with _nominatim_lock:  # 1 request per second, process-wide
        wait = 1.05 - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            res = await fetch("nominatim", s.nominatim_url, params=params, validate=_validate_nominatim, attempts=2)
        finally:
            _last_call = time.monotonic()
    best = res.payload[0] if res.payload else None
    return res, best
