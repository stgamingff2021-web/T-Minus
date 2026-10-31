"""Replay end to end (mocked as-issued archives) and officer stand-down → CAP Cancel."""
import datetime as dt
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.comms.cap import validate_cap
from app.config import get_config
from app.db.models import ReplayRun, Trigger
from app.db.session import session_scope
from app.replay import runner as replay_runner

H = 3600
PEAK = int(dt.datetime(2024, 11, 30, 18, tzinfo=dt.timezone.utc).timestamp())


def _hours(start: str, end: str) -> list[int]:
    s = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp())
    e = int(dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc).timestamp()) + 23 * H
    return list(range(s, e + 1, H))


def replay_handler(request: httpx.Request) -> httpx.Response:
    """Synthetic archive: every model forecasts 8 mm/h for the 24 h before PEAK, and each
    previous_dayN series carries that forecast (so it is visible N days ahead)."""
    u = urlparse(str(request.url))
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    times = _hours(q["start_date"], q["end_date"])
    wet = [8.0 if PEAK - 24 * H < t <= PEAK else 0.0 for t in times]
    if u.netloc == "previous-runs-api.open-meteo.com":
        if q.get("models") == "cma_grapes_global":
            return httpx.Response(400, json={"error": True, "reason": "model not available"})
        hourly = {"time": times, **{f"precipitation_previous_day{n}": list(wet) for n in range(1, 8)}}
        return httpx.Response(200, json={"latitude": 1, "longitude": 1, "hourly": hourly})
    if u.netloc == "archive-api.open-meteo.com":
        return httpx.Response(200, json={"latitude": 1, "longitude": 1, "hourly": {"time": times, "precipitation": wet}})
    return httpx.Response(404, text="unmocked")


async def test_replay_reports_lead_times_without_look_ahead(fresh_db, mock_http):
    mock_http(replay_handler)
    rid = replay_runner.start("fengal_2024")
    await replay_runner._tasks[rid]
    with session_scope() as s:
        r = s.get(ReplayRun, rid)
        assert r.status == "completed", r.error
        res = r.result
    assert res["label"].startswith("REPLAY")
    assert "cma_grapes_global" in [m["model"] for m in res["models_skipped"]]
    assert res["observed"]["peak_time"] == PEAK and res["observed"]["peak_mm"] == pytest.approx(192.0)
    # 192 mm in 24 h >= very heavy (115.6) in every model -> agreement 100% once visible within the window
    assert res["first_fire"]["ACT"] is not None
    assert res["lead_time_h"]["PREPARE"] >= 24 and res["target_met"] is True
    # no look-ahead: nothing can fire earlier than 120 h (the widest window) before the forecast event
    assert res["first_fire"]["WATCH"] >= PEAK - 24 * H - 120 * H
    # quiet period: the synthetic archive is dry then -> no false alarm
    assert not any(res["quiet_period"]["false_alarm"].values())


async def test_officer_stand_down_publishes_cap_cancel(fresh_db, mock_http):
    from app.main import app
    from tests.fakes import Router
    from tests.integration.test_agent_graph import PAGES, ScriptedLLM, make_pdf
    from app import scanner
    from app.agent.runner import create_run, execute_run
    from app.rag.ingest import ingest_pdf

    r = Router()
    d = get_config().district("chennai")
    r.wet[f"{d.hq[0]:.3f},{d.hq[1]:.3f}"] = (6.0, 40)
    mock_http(r)
    await scanner.run_scan("test")
    ingest_pdf(make_pdf(PAGES), title="Chennai DDMP (test fixture)", kind="district", district_id="chennai",
               source_url=None)
    with session_scope() as s:
        trig = s.execute(select(Trigger).where(Trigger.district_id == "chennai")).scalars().first().id
    run_id = create_run("chennai", "rain", trigger_id=trig, started_by="test", model="scripted")
    await execute_run(run_id, ScriptedLLM())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        tok = (await c.post("/api/auth/login", json={"name": "Officer", "passcode": "officer-pass"})).json()["token"]
        Hh = {"Authorization": f"Bearer {tok}"}
        run = (await c.get(f"/api/runs/{run_id}")).json()
        cap = next(x for x in run["communications"] if x["kind"] == "cap")
        conf = (await c.post(f"/api/communications/{cap['id']}/confirmation", headers=Hh)).json()
        assert (await c.post(f"/api/communications/{cap['id']}/approve", json={"confirmation_token": conf["token"]},
                             headers=Hh)).status_code == 200
        assert (await c.post(f"/api/triggers/{trig}/close", json={"confirm": False}, headers=Hh)).status_code == 400
        res = (await c.post(f"/api/triggers/{trig}/close", json={"confirm": True, "note": "Rain moved away"}, headers=Hh)).json()
        assert res["status"] == "closed" and res["cap_msg_type"] == "Cancel"
        assert res["cancel_cap"]["valid"] is True
        alerts = (await c.get("/api/public/alerts")).json()["alerts"]
        assert [a["msg_type"] for a in alerts] == ["Cancel", "Alert"]
        xml = (await c.get(alerts[0]["cap_url"])).text
        assert "<msgType>Cancel</msgType>" in xml and "<references>" in xml and validate_cap(xml)["valid"]
