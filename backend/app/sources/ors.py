"""OpenRouteService directions with ``avoid_polygons`` (safe routes). Routing: openrouteservice.

Needs a free ORS key (``ORS_API_KEY``). Avoid polygons are kept small and simple:
a 16-gon of ``at_risk_buffer_m`` around each geocoded at-risk place, max 20 polygons
(verify current ORS limits on avoid-polygon area/extent).
"""
from __future__ import annotations

import math
from typing import Any

from app.config import get_settings
from app.sources import registry
from app.sources.base import FetchResult, SourceError, fetch

MAX_AVOID = 20


def circle_polygon(lat: float, lon: float, radius_m: float, n: int = 16) -> list[list[float]]:
    """Closed ring of [lon, lat] points approximating a circle (GeoJSON order)."""
    ring = []
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
    for i in range(n):
        a = 2 * math.pi * i / n
        ring.append([round(lon + dlon * math.cos(a), 6), round(lat + dlat * math.sin(a), 6)])
    ring.append(ring[0])
    return ring


def _validate(p: Any) -> None:
    feats = p.get("features") if isinstance(p, dict) else None
    if not feats:
        raise ValueError("no route features")
    if feats[0].get("geometry", {}).get("type") != "LineString":
        raise ValueError("route geometry is not a LineString")


async def route(origin: tuple[float, float], destination: tuple[float, float],
                avoid_rings: list[list[list[float]]]) -> tuple[FetchResult, dict]:
    """origin/destination are (lat, lon). Returns (fetch, {geometry, distance_m, duration_s})."""
    s = get_settings()
    if not s.ors_api_key:
        registry.set_disabled("ors", "ORS_API_KEY not set")
        raise SourceError("ors", "Routing unavailable: ORS_API_KEY is not configured", code="not_configured",
                          retryable=False)
    body: dict[str, Any] = {
        "coordinates": [[origin[1], origin[0]], [destination[1], destination[0]]],
        "instructions": False,
    }
    rings = avoid_rings[:MAX_AVOID]
    if rings:
        body["options"] = {"avoid_polygons": {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}}
    res = await fetch("ors", s.ors_url, method="POST", json_body=body,
                      headers={"Authorization": s.ors_api_key, "Content-Type": "application/json"},
                      validate=_validate, log_params={"body": body})
    feat = res.payload["features"][0]
    summary = (feat.get("properties") or {}).get("summary") or {}
    return res, {
        "geometry": feat["geometry"],
        "distance_m": summary.get("distance"),
        "duration_s": summary.get("duration"),
        "avoided_polygons": len(rings),
    }
