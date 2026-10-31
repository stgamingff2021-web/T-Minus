"""In-process scheduler (spec §9.4): a scan every ``SCAN_INTERVAL_MIN`` minutes.

Forecasts are cached by model run (ECMWF updates every 6 h), so frequent scans are
cheap: a re-scan of an unchanged run never counts as a new run for hysteresis. The
health endpoint reports the scheduler; if it crashed, the next request to
``/api/districts`` triggers a catch-up scan (§8.4).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.config import get_settings

log = logging.getLogger("tminus.scheduler")
_task: asyncio.Task | None = None
_state: dict = {"running": False, "last_tick": None, "next_tick": None, "last_error": None, "ticks": 0}


def status() -> dict:
    s = dict(_state)
    s["enabled"] = get_settings().scheduler_enabled
    s["interval_min"] = get_settings().scan_interval_min
    s["alive"] = _task is not None and not _task.done()
    return s


async def _loop() -> None:
    from app import scanner

    settings = get_settings()
    interval = max(5, settings.scan_interval_min) * 60
    _state["running"] = True
    first = True
    while True:
        try:
            if not first or settings.scan_on_startup:
                _state["last_tick"] = dt.datetime.now(dt.timezone.utc).isoformat()
                _state["ticks"] += 1
                await scanner.run_scan("startup" if first else "schedule")
            first = False
            _state["last_error"] = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive, report via /api/health
            log.exception("scheduled scan failed")
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
        _state["next_tick"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=interval)).isoformat()
        await asyncio.sleep(interval)


def start() -> None:
    global _task
    if not get_settings().scheduler_enabled:
        return
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _state["running"] = False


def ensure_alive() -> None:
    """Catch-up: restart the loop if it died (called from read endpoints)."""
    if get_settings().scheduler_enabled and (_task is None or _task.done()):
        log.warning("scheduler not alive — restarting with a catch-up scan")
        start()
