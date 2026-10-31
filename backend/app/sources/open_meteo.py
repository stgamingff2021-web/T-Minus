"""Open-Meteo clients: Ensemble, Forecast (past_days), Flood (GloFAS), Previous Runs, Archive.

All endpoints are free and key-less (CC BY 4.0, attribution in the UI footer).
Requests batch several coordinates per call (Open-Meteo returns a JSON list for
multi-location requests). Times are requested as unix seconds in UTC.

(verify) items confirmed against https://open-meteo.com/en/docs/ensemble-api :
* per-member hourly variables are named ``precipitation`` (control) and
  ``precipitation_memberNN`` (perturbed members) for a single requested model;
* ECMWF IFS 0.25 deg ensemble = ``ecmwf_ifs025`` (51 members), GFS 0.25 deg = ``gfs025`` (31).
The parser below also accepts a ``_<model>`` suffix in case several models are requested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.config import get_settings
from app.sources.base import FetchResult, SourceError, fetch

EXPECTED_MEMBERS = {"ecmwf_ifs025": 51, "gfs025": 31, "gfs05": 31, "icon_seamless": 40, "icon_global": 40}
MODEL_LABEL = {
    "ecmwf_ifs025": "ECMWF IFS ensemble",
    "gfs025": "NCEP GFS ensemble",
    "gfs05": "NCEP GFS ensemble",
    "icon_seamless": "DWD ICON ensemble",
}
# Open-Meteo dataset names used by the static meta.json endpoint (model run times). (verify)
META_DATASET = {"ecmwf_ifs025": "ecmwf_ifs025_ensemble", "gfs025": "ncep_gefs025", "icon_seamless": "dwd_icon_eps"}

_MEMBER_SUFFIX = re.compile(r"^_member(?P<num>\d+)(?:_[a-z0-9_]+)?$")


def member_number(key: str, variable: str) -> int | None:
    """0 for the control column, N for ``<variable>_memberNN[_model]``, None otherwise."""
    if key == variable:
        return 0
    if not key.startswith(variable):
        return None
    m = _MEMBER_SUFFIX.match(key[len(variable):])
    return int(m.group("num")) if m else None


@dataclass
class EnsemblePoint:
    """Parsed per-member hourly series for one location."""

    lat: float
    lon: float
    times: np.ndarray  # int64 unix seconds
    members: np.ndarray  # (n_members, n_hours) float, NaN = missing
    member_names: list[str]
    variable: str
    elevation: float | None = None


@dataclass
class EnsembleBatch:
    model: str
    fetch: FetchResult
    points: list[EnsemblePoint] = field(default_factory=list)


def _as_list(payload: Any) -> list[dict]:
    return payload if isinstance(payload, list) else [payload]


def _key(params: dict) -> dict:
    s = get_settings()
    if s.open_meteo_api_key:
        params = {**params, "apikey": s.open_meteo_api_key}
    return params


def _coords(points: list[tuple[float, float]]) -> dict:
    return {
        "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
    }


def validate_hourly(payload: Any, variable: str, expect_n: int) -> None:
    items = _as_list(payload)
    if len(items) != expect_n:
        raise ValueError(f"expected {expect_n} locations, got {len(items)}")
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("location entry is not an object")
        if it.get("error"):
            raise ValueError(f"API error: {it.get('reason')}")
        hourly = it.get("hourly")
        if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
            raise ValueError("missing hourly.time")
        n = len(hourly["time"])
        cols = [k for k in hourly if k == variable or k.startswith(variable + "_")]
        if not cols:
            raise ValueError(f"no '{variable}' columns in response")
        for c in cols:
            if not isinstance(hourly[c], list) or len(hourly[c]) != n:
                raise ValueError(f"column {c} has wrong length")


def parse_members(item: dict, variable: str) -> EnsemblePoint:
    hourly = item["hourly"]
    times = np.asarray(hourly["time"], dtype=np.int64)
    rows: list[tuple[int, str, list]] = []
    for key, values in hourly.items():
        if key == "time":
            continue
        num = member_number(key, variable)
        if num is None:
            continue
        rows.append((num, key, values))
    rows.sort(key=lambda r: r[0])
    mat = np.array(
        [[np.nan if v is None else float(v) for v in values] for _, _, values in rows], dtype=float
    ).reshape(len(rows), len(times))
    return EnsemblePoint(
        lat=float(item.get("latitude")),
        lon=float(item.get("longitude")),
        times=times,
        members=mat,
        member_names=[k for _, k, _ in rows],
        variable=variable,
        elevation=item.get("elevation"),
    )


def check_temporal_resolution(pt: EnsemblePoint, now_ts: float) -> None:
    """Reject responses whose hourly grid is mostly empty in the first 72 h (§8.1 malformed).

    We rely on Open-Meteo delivering hourly values; if a model only filled every 3rd
    hour, rolling sums would be wrong, so the response is treated as a failure and the
    next ensemble model is tried instead.
    """
    sel = (pt.times >= now_ts) & (pt.times <= now_ts + 72 * 3600)
    if not sel.any():
        raise ValueError("no forecast hours in the next 72 h")
    all_nan = np.all(np.isnan(pt.members[:, sel]), axis=0)
    if all_nan.mean() > 0.5:
        raise ValueError("unexpected temporal resolution: most hours in the next 72 h are empty")


async def fetch_ensemble(
    points: list[tuple[float, float]], model: str, *, variable: str = "precipitation",
    past_days: int = 1, forecast_days: int = 7,
) -> EnsembleBatch:
    s = get_settings()
    params = _key({
        **_coords(points),
        "hourly": variable,
        "models": model,
        "past_days": past_days,
        "forecast_days": forecast_days,
        "timezone": "GMT",
        "timeformat": "unixtime",
    })
    import time as _time

    def _validate(p: Any) -> None:
        validate_hourly(p, variable, len(points))
        now_ts = _time.time() // 3600 * 3600
        for item in _as_list(p):
            check_temporal_resolution(parse_members(item, variable), now_ts)

    res = await fetch(
        f"ensemble:{model}", s.open_meteo_ensemble_url, params=params, validate=_validate,
        log_params={k: v for k, v in params.items() if k != "apikey"},
    )
    batch = EnsembleBatch(model=model, fetch=res)
    for item in _as_list(res.payload):
        batch.points.append(parse_members(item, variable))
    return batch


async def fetch_ensemble_multi(points: list[tuple[float, float]], model: str, variables: list[str],
                               forecast_days: int = 7) -> tuple[FetchResult, list[dict[str, EnsemblePoint]]]:
    """Several per-member variables at once (used by the WBGT heat plug-in)."""
    s = get_settings()
    params = _key({**_coords(points), "hourly": ",".join(variables), "models": model,
                   "forecast_days": forecast_days, "timezone": "GMT", "timeformat": "unixtime"})
    res = await fetch(f"ensemble:{model}", s.open_meteo_ensemble_url, params=params,
                      validate=lambda p: [validate_hourly(p, v, len(points)) for v in variables] and None,
                      log_params={k: v for k, v in params.items() if k != "apikey"})
    out = []
    for item in _as_list(res.payload):
        out.append({v: parse_members(item, v) for v in variables})
    return res, out


async def fetch_model_run_time(model: str) -> tuple[int | None, str | None]:
    """Model run initialisation time from Open-Meteo's static meta.json (verify path)."""
    s = get_settings()
    dataset = META_DATASET.get(model)
    if not dataset:
        return None, None
    url = s.open_meteo_meta_url.format(model=dataset)
    try:
        res = await fetch("open_meteo_meta", url, attempts=1, timeout_s=10,
                          validate=lambda p: p["last_run_initialisation_time"])
    except SourceError:
        return None, None
    init = res.payload.get("last_run_initialisation_time")
    return (int(init) if init else None), res.fetch_id


# ---------------------------------------------------------------------------
# Forecast API: antecedent (past 72 h) rainfall
# ---------------------------------------------------------------------------

@dataclass
class DeterministicPoint:
    lat: float
    lon: float
    times: np.ndarray
    values: np.ndarray


async def fetch_past_rain(points: list[tuple[float, float]], past_days: int = 3) -> tuple[FetchResult, list[DeterministicPoint]]:
    s = get_settings()
    params = _key({**_coords(points), "hourly": "precipitation", "past_days": past_days, "forecast_days": 1,
                   "timezone": "GMT", "timeformat": "unixtime"})
    res = await fetch("forecast", s.open_meteo_forecast_url, params=params,
                      validate=lambda p: validate_hourly(p, "precipitation", len(points)),
                      log_params={k: v for k, v in params.items() if k != "apikey"})
    out = []
    for item in _as_list(res.payload):
        h = item["hourly"]
        out.append(DeterministicPoint(
            lat=float(item["latitude"]), lon=float(item["longitude"]),
            times=np.asarray(h["time"], dtype=np.int64),
            values=np.array([np.nan if v is None else float(v) for v in h["precipitation"]], dtype=float),
        ))
    return res, out


# ---------------------------------------------------------------------------
# Flood API (GloFAS v4)
# ---------------------------------------------------------------------------

FLOOD_DAILY = [
    "river_discharge", "river_discharge_mean", "river_discharge_median",
    "river_discharge_max", "river_discharge_min", "river_discharge_p25", "river_discharge_p75",
]


def validate_daily(payload: Any, expect_n: int, var: str = "river_discharge") -> None:
    items = _as_list(payload)
    if len(items) != expect_n:
        raise ValueError(f"expected {expect_n} locations, got {len(items)}")
    for it in items:
        if it.get("error"):
            raise ValueError(f"API error: {it.get('reason')}")
        daily = it.get("daily")
        if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
            raise ValueError("missing daily.time")
        if var not in daily:
            raise ValueError(f"missing daily.{var}")


@dataclass
class FloodPoint:
    lat: float
    lon: float
    times: np.ndarray
    series: dict[str, np.ndarray]

    def all_null(self) -> bool:
        return all(np.all(np.isnan(v)) for v in self.series.values())


def _daily_arrays(item: dict, keys: list[str]) -> FloodPoint:
    d = item["daily"]
    return FloodPoint(
        lat=float(item["latitude"]), lon=float(item["longitude"]),
        times=np.asarray(d["time"], dtype=np.int64),
        series={k: np.array([np.nan if v is None else float(v) for v in d.get(k, [None] * len(d["time"]))])
                for k in keys if k in d},
    )


async def fetch_flood_forecast(points: list[tuple[float, float]], forecast_days: int = 30) -> tuple[FetchResult, list[FloodPoint]]:
    s = get_settings()
    params = _key({**_coords(points), "daily": ",".join(FLOOD_DAILY), "forecast_days": forecast_days,
                   "timeformat": "unixtime"})
    res = await fetch("flood", s.open_meteo_flood_url, params=params,
                      validate=lambda p: validate_daily(p, len(points)),
                      log_params={k: v for k, v in params.items() if k != "apikey"})
    return res, [_daily_arrays(it, FLOOD_DAILY) for it in _as_list(res.payload)]


async def fetch_flood_reanalysis(lat: float, lon: float, start: str, end: str) -> tuple[FetchResult, FloodPoint]:
    """Daily GloFAS reanalysis discharge for one point (baseline percentiles)."""
    s = get_settings()
    params = _key({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "daily": "river_discharge",
                   "start_date": start, "end_date": end, "models": "consolidated_v4", "timeformat": "unixtime"})
    try:
        res = await fetch("flood", s.open_meteo_flood_url, params=params, validate=lambda p: validate_daily(p, 1),
                          timeout_s=90, log_params={k: v for k, v in params.items() if k != "apikey"})
    except SourceError as exc:
        if exc.simulated or exc.http_code != 400:
            raise
        params.pop("models")  # model name not accepted -> default (seamless) archive
        res = await fetch("flood", s.open_meteo_flood_url, params=params, validate=lambda p: validate_daily(p, 1),
                          timeout_s=90, log_params={k: v for k, v in params.items() if k != "apikey"})
    return res, _daily_arrays(_as_list(res.payload)[0], ["river_discharge"])


# ---------------------------------------------------------------------------
# Replay: Previous Runs API (forecasts as issued) + Archive (ground truth)
# ---------------------------------------------------------------------------

async def fetch_previous_runs(lat: float, lon: float, model: str, start: str, end: str,
                              days: range = range(1, 8)) -> tuple[FetchResult, dict]:
    """Hourly precipitation as issued N days before each valid time (``_previous_dayN``).

    ``precipitation_previous_dayN`` at valid time v comes from a model run initialised
    at least N*24 h before v; replay picks N so that the run predates the replay clock.
    The day-0 series (closest to an analysis) is deliberately never requested.
    """
    s = get_settings()
    hourly = ",".join(f"precipitation_previous_day{n}" for n in days)
    params = _key({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": hourly, "models": model,
                   "start_date": start, "end_date": end, "timezone": "GMT", "timeformat": "unixtime"})

    def _validate(p: Any) -> None:
        it = _as_list(p)[0]
        if it.get("error"):
            raise ValueError(it.get("reason"))
        if "hourly" not in it or "time" not in it["hourly"]:
            raise ValueError("missing hourly.time")

    res = await fetch("previous_runs", s.open_meteo_previous_runs_url, params=params, validate=_validate,
                      timeout_s=60, log_params={k: v for k, v in params.items() if k != "apikey"})
    return res, _as_list(res.payload)[0]


async def fetch_archive_rain(lat: float, lon: float, start: str, end: str) -> tuple[FetchResult, DeterministicPoint]:
    s = get_settings()
    params = _key({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": "precipitation",
                   "start_date": start, "end_date": end, "timezone": "GMT", "timeformat": "unixtime"})
    res = await fetch("archive", s.open_meteo_archive_url, params=params,
                      validate=lambda p: validate_hourly(p, "precipitation", 1), timeout_s=60,
                      log_params={k: v for k, v in params.items() if k != "apikey"})
    it = _as_list(res.payload)[0]
    h = it["hourly"]
    return res, DeterministicPoint(
        lat=float(it["latitude"]), lon=float(it["longitude"]), times=np.asarray(h["time"], dtype=np.int64),
        values=np.array([np.nan if v is None else float(v) for v in h["precipitation"]], dtype=float),
    )
