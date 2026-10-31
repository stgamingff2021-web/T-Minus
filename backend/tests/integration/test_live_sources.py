"""Live checks against the REAL public APIs, with schema validation (spec §10.2).

Marked ``live``; each test skips automatically when the host is unreachable (e.g. a
sandbox whose network policy blocks it). Run:  pytest -m live tests/integration/test_live_sources.py
"""
import time

import httpx
import pytest

from app.sources import base
from app.sources.open_meteo import fetch_ensemble, fetch_flood_forecast, fetch_past_rain
from app.sources.sachet import fetch_alerts

pytestmark = pytest.mark.live
CHENNAI = (13.083, 80.270)


def reachable(url: str) -> bool:
    try:
        httpx.get(url, timeout=8)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(autouse=True)
def real_http(fresh_db):
    base._client = None
    yield
    base._client = None


async def test_open_meteo_ensemble_live():
    if not reachable("https://ensemble-api.open-meteo.com/v1/ensemble?latitude=13&longitude=80&hourly=precipitation&forecast_days=1"):
        pytest.skip("Open-Meteo ensemble API unreachable from this environment")
    t0 = time.time()
    b = await fetch_ensemble([CHENNAI], "ecmwf_ifs025")
    pt = b.points[0]
    assert pt.members.shape[0] == 51, f"expected 51 ECMWF members, got {pt.members.shape[0]}"
    assert pt.members.shape[1] >= 24 * 7
    assert time.time() - t0 < 60


async def test_open_meteo_forecast_and_flood_live():
    if not reachable("https://api.open-meteo.com/v1/forecast?latitude=13&longitude=80&hourly=precipitation&forecast_days=1"):
        pytest.skip("Open-Meteo forecast API unreachable")
    _, pts = await fetch_past_rain([CHENNAI])
    assert len(pts[0].times) >= 72
    _, fps = await fetch_flood_forecast([(10.86, 78.69)])  # Cauvery at Srirangam
    assert len(fps[0].times) >= 7


async def test_sachet_feed_live():
    if not reachable("https://sachet.ndma.gov.in/"):
        pytest.skip("SACHET unreachable")
    alerts, fetch_id = await fetch_alerts()
    assert fetch_id
    for a in alerts:
        assert a.identifier is not None
