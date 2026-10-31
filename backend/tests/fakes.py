"""Tiny synthetic API responses for tests only (shape of Open-Meteo / SACHET / Overpass).

They never ship in application code and are never shown by the app (§10.2).
"""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx

HOUR = 3600


def day_start(ts: float) -> int:
    return int(ts // 86400 * 86400)


def ensemble_payload(lat_csv: str, lon_csv: str, n_members: int, wet: dict[int, tuple[float, int]],
                     hours: int = 192, jitter: float = 0.0):
    """wet = {point_index: (mm_per_hour, n_wet_members)} -> 24 wet hours starting ~36 h from now."""
    lats = [float(x) for x in lat_csv.split(",")]
    lons = [float(x) for x in lon_csv.split(",")]
    start = day_start(time.time()) - 86400  # past_days=1
    now_idx = int((time.time() - start) // HOUR)
    out = []
    for i, (la, lo) in enumerate(zip(lats, lons)):
        hourly = {"time": [start + h * HOUR for h in range(hours)]}
        for m in range(n_members):
            key = "precipitation" if m == 0 else f"precipitation_member{m:02d}"
            vals = [jitter] * hours
            if i in wet and m < wet[i][1]:
                for h in range(now_idx + 20, now_idx + 44):
                    vals[h] = wet[i][0]
            hourly[key] = vals
        out.append({"latitude": la, "longitude": lo, "elevation": 5.0, "hourly": hourly,
                    "hourly_units": {"precipitation": "mm"}})
    return out if len(out) > 1 else out[0]


def past_rain_payload(lat_csv: str, lon_csv: str, mm_per_hour: float = 0.0):
    lats = lat_csv.split(",")
    start = day_start(time.time()) - 3 * 86400
    out = []
    for la, lo in zip(lats, lon_csv.split(",")):
        out.append({"latitude": float(la), "longitude": float(lo),
                    "hourly": {"time": [start + h * HOUR for h in range(96)], "precipitation": [mm_per_hour] * 96}})
    return out if len(out) > 1 else out[0]


def flood_payload(lat_csv: str, lon_csv: str, value: float = 10.0, days: int = 30, null: bool = False):
    start = day_start(time.time())
    out = []
    keys = ["river_discharge", "river_discharge_mean", "river_discharge_median", "river_discharge_max",
            "river_discharge_min", "river_discharge_p25", "river_discharge_p75"]
    for la, lo in zip(lat_csv.split(","), lon_csv.split(",")):
        daily = {"time": [start + d * 86400 for d in range(days)]}
        for k in keys:
            daily[k] = [None if null else value] * days
        out.append({"latitude": float(la), "longitude": float(lo), "daily": daily})
    return out if len(out) > 1 else out[0]


def flood_reanalysis_payload(lat: str, lon: str, n_days: int = 2000):
    start = 441763200  # 1984-01-01
    return {"latitude": float(lat), "longitude": float(lon),
            "daily": {"time": [start + d * 86400 for d in range(n_days)],
                      "river_discharge": [float(d % 100) for d in range(n_days)]}}


EMPTY_RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>SACHET</title></channel></rss>"""


def rss_with_cap(cap_xml: str) -> str:
    import email.utils

    pub = email.utils.formatdate(time.time() - 3600, usegmt=True)
    return f"""<?xml version="1.0"?><rss version="2.0"><channel><title>SACHET</title>
<item><title>Orange alert</title><link>https://example.invalid/cap1</link><description>x</description>
<author>controlroom@ndma.gov.in (IMD Chennai)</author><guid>cap1</guid>
<pubDate>{pub}</pubDate></item></channel></rss>""", cap_xml


def cap_orange(area: str = "Chennai, Tiruvallur", expires: str = "2099-01-01T00:00:00+05:30") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>X1</identifier><sender>imd</sender>
<sent>2026-10-31T06:00:00+05:30</sent><status>Actual</status><msgType>Alert</msgType><scope>Public</scope>
<info><category>Met</category><event>Heavy Rainfall</event><urgency>Expected</urgency><severity>Severe</severity>
<certainty>Likely</certainty><expires>{expires}</expires><senderName>IMD Chennai</senderName>
<headline>Orange alert: heavy rain</headline><description>Heavy to very heavy rainfall likely.</description>
<area><areaDesc>{area}</areaDesc></area></info></alert>"""


class Router:
    """Configurable fake upstream. Toggle attributes between scans to simulate changes."""

    def __init__(self):
        self.wet: dict[str, tuple[float, int]] = {}  # district hq "lat,lon" -> (rate, members)
        self.jitter = 0.0
        self.fail: set[str] = set()  # hosts to fail with 503
        self.sachet_cap: str | None = None
        self.calls: list[str] = []

    def _wet_index(self, lat_csv: str, lon_csv: str) -> dict[int, tuple[float, int]]:
        out = {}
        for i, (la, lo) in enumerate(zip(lat_csv.split(","), lon_csv.split(","))):
            key = f"{float(la):.3f},{float(lo):.3f}"
            if key in self.wet:
                out[i] = self.wet[key]
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        u = urlparse(str(request.url))
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        host = u.netloc
        self.calls.append(f"{host}{u.path}")
        model = q.get("models", "")
        if host in self.fail or f"{host}:{model}" in self.fail:
            return httpx.Response(503, text="upstream unavailable")
        if host == "ensemble-api.open-meteo.com" and u.path.startswith("/data/"):
            return httpx.Response(404, text="not found")
        if host == "ensemble-api.open-meteo.com":
            n = 51 if model == "ecmwf_ifs025" else 31
            return httpx.Response(200, json=ensemble_payload(q["latitude"], q["longitude"], n,
                                                             self._wet_index(q["latitude"], q["longitude"]),
                                                             jitter=self.jitter))
        if host == "api.open-meteo.com":
            return httpx.Response(200, json=past_rain_payload(q["latitude"], q["longitude"]))
        if host == "flood-api.open-meteo.com":
            if "start_date" in q:
                return httpx.Response(200, json=flood_reanalysis_payload(q["latitude"], q["longitude"]))
            return httpx.Response(200, json=flood_payload(q["latitude"], q["longitude"]))
        if host == "sachet.ndma.gov.in":
            if self.sachet_cap:
                rss, _ = rss_with_cap(self.sachet_cap)
                return httpx.Response(200, text=rss)
            return httpx.Response(200, text=EMPTY_RSS)
        if host == "example.invalid" and self.sachet_cap:
            return httpx.Response(200, text=self.sachet_cap)
        if host == "overpass-api.de":
            return httpx.Response(200, json={"elements": [
                {"type": "node", "id": 101, "lat": 13.05, "lon": 80.25,
                 "tags": {"amenity": "school", "name": "Corporation Higher Secondary School"}},
                {"type": "way", "id": 202, "center": {"lat": 13.06, "lon": 80.24},
                 "tags": {"amenity": "hospital", "name": "Rajiv Gandhi Government General Hospital"}}]})
        if host == "nominatim.openstreetmap.org":
            return httpx.Response(200, json=[{"lat": "13.0", "lon": "80.2", "importance": 0.6, "osm_type": "way",
                                              "osm_id": 7, "display_name": "Velachery, Chennai"}])
        return httpx.Response(404, text=json.dumps({"error": "unmocked", "url": str(request.url)}))
