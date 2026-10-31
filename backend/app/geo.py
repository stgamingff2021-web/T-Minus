"""Geography helpers: district boundaries, boxes, CAP polygons.

Boundaries are downloaded by ``scripts/fetch_boundaries.py`` from a published source
(OpenStreetMap via Overpass, or geoBoundaries) into ``data/boundaries/`` together with
a provenance file. Nothing here invents geometry: without a downloaded boundary the
app falls back to the district HQ point and a configured search radius, and says so.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from app.config import DistrictCfg, get_config, get_settings

BOUNDARY_FILE = "boundaries/districts.geojson"
PROVENANCE_FILE = "boundaries/PROVENANCE.json"


def boundaries_path() -> Path:
    return get_settings().data_dir / BOUNDARY_FILE


def load_boundaries() -> tuple[dict[str, dict], dict | None]:
    """Return ({district_id: geometry}, provenance) from the downloaded file, if present."""
    p = boundaries_path()
    if not p.exists():
        return {}, None
    fc = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for f in fc.get("features", []):
        did = (f.get("properties") or {}).get("district_id")
        if did and f.get("geometry"):
            out[did] = f["geometry"]
    prov_p = get_settings().data_dir / PROVENANCE_FILE
    prov = json.loads(prov_p.read_text(encoding="utf-8")) if prov_p.exists() else None
    return out, prov


def geometry_of(boundary: dict | None) -> BaseGeometry | None:
    if not boundary:
        return None
    try:
        g = shape(boundary)
        return g if g.is_valid else g.buffer(0)
    except Exception:  # noqa: BLE001 - malformed geometry is treated as missing
        return None


def radius_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """(south, west, north, east) box around a point."""
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.1, math.cos(math.radians(lat))))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def district_box(d: DistrictCfg, boundary: dict | None) -> tuple[float, float, float, float]:
    g = geometry_of(boundary)
    if g is not None:
        minx, miny, maxx, maxy = g.bounds
        return (miny, minx, maxy, maxx)
    return radius_box(d.hq[0], d.hq[1], get_config().search_radius_km(d))


def contains(boundary: dict | None, lat: float, lon: float, d: DistrictCfg | None = None) -> bool | None:
    """Point-in-district test; None when no boundary is loaded (unknown)."""
    g = geometry_of(boundary)
    if g is None:
        if d is None:
            return None
        s, w, n, e = radius_box(d.hq[0], d.hq[1], get_config().search_radius_km(d))
        return s <= lat <= n and w <= lon <= e
    from shapely.geometry import Point

    return bool(g.buffer(0.01).contains(Point(lon, lat)))


def cap_polygon(boundary: dict | None, max_points: int = 100) -> str | None:
    """Simplified outer ring as a CAP polygon string 'lat,lon lat,lon ...' (closed)."""
    g = geometry_of(boundary)
    if g is None:
        return None
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda x: x.area)
    tol = 0.001
    simp = g.simplify(tol, preserve_topology=True)
    while len(simp.exterior.coords) > max_points and tol < 0.2:
        tol *= 2
        simp = g.simplify(tol, preserve_topology=True)
    coords = list(simp.exterior.coords)
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    if len(coords) < 4:
        return None
    return " ".join(f"{lat:.4f},{lon:.4f}" for lon, lat in coords)


def simplified_geojson(boundary: dict | None, tol: float = 0.003) -> dict | None:
    g = geometry_of(boundary)
    if g is None:
        return None
    return mapping(g.simplify(tol, preserve_topology=True))


def centroid(boundary: dict | None) -> tuple[float, float] | None:
    g = geometry_of(boundary)
    if g is None:
        return None
    c = g.representative_point()
    return (c.y, c.x)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def feature(geometry: dict, **props: Any) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": props}
