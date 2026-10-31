"""Features 2 and 4 with mocked upstreams (synthetic payloads exist only in tests)."""
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from app.config import get_config
from app.db.models import Assessment
from app.db.session import session_scope
from app.main import create_app

HOUR = 3600


def wind_payload(lat, lon, n=51, strong=10, peak=70.0):
    start = int(time.time() // HOUR * HOUR) - 2 * HOUR
    hourly = {"time": [start + h * HOUR for h in range(72)]}
    for m in range(n):
        key = "wind_speed_10m" if m == 0 else f"wind_speed_10m_member{m:02d}"
        vals = [20.0] * 72
        if m < strong:
            vals[30] = peak
        hourly[key] = vals
    return {"latitude": lat, "longitude": lon, "hourly": hourly}


def seed_rain(probability=0.6, saturated=True):
    with session_scope() as s:
        s.add(Assessment(district_id="chennai", hazard="rain", model="ecmwf_ifs025", model_run_id="run-1",
                         n_members=51, n_valid=51, level="PREPARE",
                         probabilities={"very_heavy@72h": {"category": "very_heavy", "threshold_mm": 115.6, "within_h": 72,
                                                           "probability": probability, "n_exceed": 31, "n_valid": 51}},
                         details={"headline": {"category": "very_heavy", "threshold_mm": 115.6, "probability": probability,
                                               "n_exceed": 31, "n_valid": 51, "within_h": 72, "t_star": int(time.time()) + 30 * HOUR}},
                         t_minus_h=30.0, flags={"saturated_ground": saturated, "antecedent": {"mm_72h": 120.0}}))


class Upstream:
    def __init__(self, elevation_fails=False, wind_fails=False):
        self.calls = {"elevation": 0, "ensemble": 0, "reverse": 0, "overpass": 0}
        self.elevation_fails, self.wind_fails = elevation_fails, wind_fails
        self.overpass_elements = []
        d = get_config().district("chennai")
        self.lat0 = d.hq[0] - 0.2

    def elevation(self, lat):
        return round(1.0 + max(0.0, (lat - self.lat0)) * 300, 1)  # rises to the north

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if "/v1/elevation" in url:
            self.calls["elevation"] += 1
            if self.elevation_fails:
                return httpx.Response(500, text="down")
            lats = [float(x) for x in q["latitude"].split(",")]
            return httpx.Response(200, json={"elevation": [self.elevation(la) for la in lats]})
        if "ensemble" in url:
            self.calls["ensemble"] += 1
            if self.wind_fails:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, json=wind_payload(float(q["latitude"]), float(q["longitude"])))
        if "/reverse" in url:
            self.calls["reverse"] += 1
            return httpx.Response(200, json={"name": "", "place_rank": 18, "osm_type": "node", "osm_id": 1,
                                             "display_name": "Somewhere, Chennai",
                                             "address": {"suburb": f"Area {q['lat'][:7]}", "city": "Chennai"}})
        if "overpass" in url or "interpreter" in url:
            self.calls["overpass"] += 1
            return httpx.Response(200, json={"elements": self.overpass_elements})
        return httpx.Response(404, text="unexpected " + url)


def test_impact_engine_scores_cells_from_terrain_and_live_hazard(fresh_db, mock_http):
    seed_rain()
    up = Upstream()
    mock_http(up)
    c = TestClient(create_app())
    data = c.get("/api/districts/chennai/impact").json()
    cells = data["cells"]
    assert len(cells) >= 20 and data["boundary"] == "radius around HQ"
    assert data["wind"]["available"] and data["wind"]["n_cyclonic"] == 10 and data["wind"]["members"] == 51
    assert data["hazard_index"] == round(min(1.0, 0.6 * 1.2), 3)  # max(rain 60%, wind 20%) x saturation
    for cell in cells:  # impact = H x (0.4 + 0.6 S), banded by the configured classes
        assert cell["impact"] == round(data["hazard_index"] * (0.4 + 0.6 * cell["susceptibility"]), 3)
    lowest = min(cells, key=lambda x: x["elevation_m"])
    assert "lowest 10% of the district" in lowest["factors"] and lowest["class"] in ("high", "medium")
    assert data["areas"] and data["areas"][0]["name"].endswith("Chennai") and data["areas"][0]["settlement"] == "urban"
    assert sum(data["counts"].values()) == len(cells)

    n_elev = up.calls["elevation"]
    again = c.get("/api/districts/chennai/impact").json()
    assert again["id"] == data["id"] and up.calls["elevation"] == n_elev  # cached for the same model run


def test_no_terrain_means_no_cells_and_wind_failure_is_reported(fresh_db, mock_http):
    seed_rain(probability=0.3, saturated=False)
    mock_http(Upstream(elevation_fails=True, wind_fails=True))
    data = TestClient(create_app()).get("/api/districts/chennai/impact").json()
    assert data["cells"] == [] and data["areas"] == [] and data["terrain_error"]
    assert data["wind"]["available"] is False and data["hazard_index"] == 0.3


def test_assets_at_risk_get_measures_and_a_safer_relocation_option(fresh_db, mock_http):
    seed_rain()
    up = Upstream()
    mock_http(up)
    c = TestClient(create_app())
    cells = c.get("/api/districts/chennai/impact").json()["cells"]
    worst = max(cells, key=lambda x: x["impact"])
    best = min(cells, key=lambda x: x["impact"])
    up.overpass_elements = [
        {"type": "node", "id": 1, "lat": worst["lat"], "lon": worst["lon"], "tags": {"name": "TNCSC Godown South"}},
        {"type": "node", "id": 2, "lat": best["lat"], "lon": best["lon"], "tags": {"name": "FCI Godown North"}},
        {"type": "node", "id": 3, "lat": best["lat"], "lon": best["lon"], "tags": {"amenity": "hospital", "name": "GH"}},
        {"type": "node", "id": 4, "lat": worst["lat"], "lon": worst["lon"], "tags": {"shop": "bakery", "name": "Not an asset"}},
    ]
    data = c.get("/api/districts/chennai/assets").json()
    by_name = {a["name"]: a for a in data["assets"]}
    assert set(by_name) == {"TNCSC Godown South", "FCI Godown North", "GH"}
    south, north = by_name["TNCSC Godown South"], by_name["FCI Godown North"]
    assert south["type"] == "grain_storage" and south["risk"] in ("high", "medium") and south["measures"]
    assert south["urgent"] is False  # expected impact in 30 h; "urgent" means within 24 h
    rank = {"high": 0, "medium": 1, "low": 2}
    assert rank[north["risk"]] > rank[south["risk"]]  # district-wide hazard: north is safer, not risk-free
    option = south["relocation"][0]
    assert option["name"] == "FCI Godown North" and option["distance_km"] > 0 and option["route"] == "not checked"
    assert sum(data["counts"].values()) == 3 and data["osm_error"] is None
