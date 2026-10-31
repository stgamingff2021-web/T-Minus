"""In-memory source registry: live status for the Sources panel + the chaos switch.

Status vocabulary (spec §7.2 Sources panel):
  live              last call succeeded and the data is younger than the source's stale age
  stale             last success is older than the stale age (or the last call failed but a
                    previous real reading exists — it is shown with its timestamp)
  failed            the last call failed and no successful reading exists in this window
  simulated outage  the admin chaos switch is forcing this source to fail
  never fetched     not used yet in this process (e.g. Nominatim before the first geocode)
  disabled          not configured (e.g. IMD without IP whitelisting, ORS without a key)

The registry is rebuilt from ``source_fetches`` at startup, so it survives restarts.
"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field

from app.db.models import utcnow


@dataclass
class SourceMeta:
    key: str
    name: str
    provider: str
    used_for: str
    stale_after_min: int
    chaos_allowed: bool = True
    attribution: str | None = None


SOURCES: dict[str, SourceMeta] = {
    m.key: m
    for m in [
        SourceMeta("ensemble:ecmwf_ifs025", "ECMWF IFS ensemble (51 members)", "Open-Meteo Ensemble API",
                   "Rain probabilities (primary)", 7 * 60, attribution="Weather data: Open-Meteo (CC BY 4.0)"),
        SourceMeta("ensemble:gfs025", "NCEP GFS ensemble (31 members)", "Open-Meteo Ensemble API",
                   "Rain probabilities (fallback)", 7 * 60, attribution="Weather data: Open-Meteo (CC BY 4.0)"),
        SourceMeta("ensemble:icon_seamless", "DWD ICON ensemble", "Open-Meteo Ensemble API",
                   "Rain probabilities (fallback)", 7 * 60, attribution="Weather data: Open-Meteo (CC BY 4.0)"),
        SourceMeta("open_meteo_meta", "Model run metadata (meta.json)", "Open-Meteo",
                   "Model run initialisation times (hysteresis counts new runs)", 7 * 60, chaos_allowed=False,
                   attribution="Weather data: Open-Meteo (CC BY 4.0)"),
        SourceMeta("forecast", "Deterministic forecast + past 72 h", "Open-Meteo Forecast API",
                   "Antecedent rainfall (saturated ground)", 3 * 60, attribution="Weather data: Open-Meteo (CC BY 4.0)"),
        SourceMeta("flood", "GloFAS v4 river discharge", "Open-Meteo Flood API",
                   "River trigger and baselines", 26 * 60, attribution="Flood data: GloFAS via Open-Meteo (CC BY 4.0)"),
        SourceMeta("sachet", "NDMA SACHET CAP feed", "National Disaster Management Authority",
                   "Official alert floor and cross-check", 30),
        SourceMeta("imd", "IMD district warnings", "India Meteorological Department",
                   "Optional cross-check (IP whitelisting)", 6 * 60),
        SourceMeta("overpass", "OpenStreetMap facilities", "Overpass API",
                   "Shelters, hospitals, schools; facility verification", 25 * 60,
                   attribution="© OpenStreetMap contributors"),
        SourceMeta("nominatim", "OpenStreetMap geocoder", "Nominatim",
                   "Locating vulnerable places named in plans", 7 * 24 * 60, attribution="© OpenStreetMap contributors"),
        SourceMeta("ors", "Routing with avoid-polygons", "openrouteservice",
                   "Safe routes around at-risk areas", 24 * 60, attribution="Routing: openrouteservice"),
        SourceMeta("previous_runs", "Forecasts archived as issued", "Open-Meteo Previous Runs API",
                   "Replay mode only", 365 * 24 * 60, attribution="Weather data: Open-Meteo (CC BY 4.0)"),
        SourceMeta("archive", "Reanalysis archive (ground truth)", "Open-Meteo Historical Weather API",
                   "Replay ground truth", 365 * 24 * 60, attribution="Weather data: Open-Meteo (CC BY 4.0)"),
        SourceMeta("llm:primary", "Agent LLM (primary)", "Anthropic Claude",
                   "Agent reasoning, drafting", 24 * 60),
        SourceMeta("llm:backup", "Agent LLM (backup)", "Anthropic Claude",
                   "Agent reasoning when primary fails", 24 * 60),
    ]
}


@dataclass
class SourceState:
    last_fetch_at: dt.datetime | None = None
    last_ok_at: dt.datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_latency_ms: int | None = None
    last_fetch_id: str | None = None
    last_ok_fetch_id: str | None = None
    model_run: str | None = None
    calls: int = 0
    failures: int = 0
    latencies: list[int] = field(default_factory=list)
    disabled_reason: str | None = None


_lock = threading.Lock()
_state: dict[str, SourceState] = {}
_chaos: set[str] = set()


def _st(source: str) -> SourceState:
    if source not in _state:
        _state[source] = SourceState()
    return _state[source]


def observe(source: str, *, status: str, latency_ms: int | None = None, error: str | None = None,
            simulated: bool = False, fetch_id: str | None = None, at: dt.datetime | None = None) -> None:
    with _lock:
        st = _st(source)
        now = at or utcnow()
        st.last_fetch_at = now
        st.last_status = status
        st.last_fetch_id = fetch_id
        st.calls += 1
        if latency_ms is not None:
            st.last_latency_ms = latency_ms
            st.latencies = (st.latencies + [latency_ms])[-20:]
        if status == "ok":
            st.last_ok_at = now
            st.last_ok_fetch_id = fetch_id
            st.last_error = None
        else:
            st.failures += 1
            st.last_error = error


def set_model_run(source: str, run: str | None) -> None:
    with _lock:
        _st(source).model_run = run


def set_disabled(source: str, reason: str | None) -> None:
    with _lock:
        _st(source).disabled_reason = reason


def set_chaos(source: str, enabled: bool) -> None:
    if source not in SOURCES or not SOURCES[source].chaos_allowed:
        raise KeyError(source)
    with _lock:
        (_chaos.add if enabled else _chaos.discard)(source)


def is_chaos(source: str) -> bool:
    return source in _chaos


def chaos_sources() -> list[str]:
    return sorted(_chaos)


def status_of(source: str, now: dt.datetime | None = None) -> dict:
    now = now or utcnow()
    meta = SOURCES.get(source)
    with _lock:
        st = _st(source)
        if source in _chaos:
            status = "simulated outage"
        elif st.disabled_reason and st.last_fetch_at is None:
            status = "disabled"
        elif st.last_fetch_at is None:
            status = "never fetched"
        elif st.last_status == "ok":
            age_min = (now - st.last_ok_at).total_seconds() / 60 if st.last_ok_at else 1e9
            status = "live" if (meta is None or age_min <= meta.stale_after_min) else "stale"
        elif st.last_ok_at is not None:
            status = "stale"
        else:
            status = "failed"
        return {
            "key": source,
            "name": meta.name if meta else source,
            "provider": meta.provider if meta else None,
            "used_for": meta.used_for if meta else None,
            "attribution": meta.attribution if meta else None,
            "chaos_allowed": bool(meta and meta.chaos_allowed),
            "status": status,
            "last_status": st.last_status,
            "last_fetch_at": st.last_fetch_at.isoformat() if st.last_fetch_at else None,
            "last_ok_at": st.last_ok_at.isoformat() if st.last_ok_at else None,
            "last_error": st.last_error,
            "latency_ms": st.last_latency_ms,
            "latency_history": list(st.latencies),
            "last_fetch_id": st.last_fetch_id,
            "last_ok_fetch_id": st.last_ok_fetch_id,
            "model_run": st.model_run,
            "calls": st.calls,
            "failures": st.failures,
            "disabled_reason": st.disabled_reason,
            "stale_after_min": meta.stale_after_min if meta else None,
        }


def all_statuses() -> list[dict]:
    keys = list(SOURCES.keys()) + [k for k in _state if k not in SOURCES]
    return [status_of(k) for k in keys]


def warm_from_db() -> None:
    """Rebuild last-known status from source_fetches so the panel survives restarts."""
    from sqlalchemy import func, select

    from app.db.models import SourceFetch
    from app.db.session import session_scope

    with session_scope() as s:
        latest = s.execute(
            select(SourceFetch.source, func.max(SourceFetch.fetched_at)).group_by(SourceFetch.source)
        ).all()
        for source, _ in latest:
            rows = s.execute(
                select(SourceFetch).where(SourceFetch.source == source)
                .order_by(SourceFetch.fetched_at.desc()).limit(20)
            ).scalars().all()
            for r in reversed(rows):
                if r.simulated:
                    continue
                observe(source, status=r.status, latency_ms=r.latency_ms, error=r.error,
                        fetch_id=r.id, at=_aware(r.fetched_at))
                if r.model_run_id:
                    set_model_run(source, r.model_run_id)


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def reset() -> None:
    """Test helper."""
    with _lock:
        _state.clear()
        _chaos.clear()
