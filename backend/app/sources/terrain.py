"""Ground elevation (Copernicus DEM, 90 m) from the Open-Meteo Elevation API.

Up to 100 coordinates per request. Terrain does not change, so callers cache values forever.
"""
from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.sources.base import FetchResult, fetch

MAX_POINTS = 100


async def fetch_elevation(points: list[tuple[float, float]]) -> tuple[FetchResult, list[float | None]]:
    if not points or len(points) > MAX_POINTS:
        raise ValueError(f"1..{MAX_POINTS} points per request")

    def _validate(p: Any) -> None:
        if not isinstance(p, dict) or not isinstance(p.get("elevation"), list) or len(p["elevation"]) != len(points):
            raise ValueError("expected an 'elevation' list with one value per point")

    params = {"latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
              "longitude": ",".join(f"{lon:.4f}" for _, lon in points)}
    res = await fetch("elevation", get_settings().open_meteo_elevation_url, params=params, validate=_validate)
    return res, [None if v is None else float(v) for v in res.payload["elevation"]]
