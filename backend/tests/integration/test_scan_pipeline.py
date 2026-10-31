"""Scan pipeline end to end against a mocked upstream: AC-01..03, AC-06, AC-11 behaviour."""
import pytest
from sqlalchemy import select

from app import scanner
from app.config import get_config
from app.db.models import Assessment, SourceFetch, Trigger
from app.db.session import session_scope
from app.sources import registry
from tests.fakes import Router, cap_orange


def hq_key(district_id: str) -> str:
    d = get_config().district(district_id)
    return f"{d.hq[0]:.3f},{d.hq[1]:.3f}"


def rain(s, district_id):
    return s.execute(select(Assessment).where(Assessment.district_id == district_id, Assessment.hazard == "rain")
                     .order_by(Assessment.computed_at.desc())).scalars().first()


def triggers(s, district_id):
    return s.execute(select(Trigger).where(Trigger.district_id == district_id, Trigger.hazard == "rain")).scalars().all()


@pytest.fixture
def upstream(fresh_db, mock_http):
    r = Router()
    mock_http(r)
    return r


async def test_scan_computes_levels_and_opens_exactly_one_trigger(upstream):
    upstream.wet[hq_key("chennai")] = (6.0, 40)  # 40 of 51 members: 144 mm in 24 h
    res = await scanner.run_scan("test")
    assert res["status"] in ("ok", "partial")
    assert res["districts_ok"] == len(get_config().districts)
    with session_scope() as s:
        a = rain(s, "chennai")
        p = a.probabilities["very_heavy@48h"]
        assert (p["n_exceed"], p["n_valid"]) == (40, 51)  # AC-02 numbers straight from members
        assert a.level == "ACT" and a.model == "ecmwf_ifs025"
        assert a.fetch_ids and s.get(SourceFetch, a.fetch_ids[0]).raw_payload is not None
        assert len(triggers(s, "chennai")) == 1
        assert rain(s, "madurai").level is None
    # AC-03: the next scan does not duplicate it, and re-scanning the same run is not a new run
    await scanner.run_scan("test")
    with session_scope() as s:
        ts = triggers(s, "chennai")
        assert len(ts) == 1 and ts[0].level == "ACT"
        a1, a2 = s.execute(select(Assessment).where(Assessment.district_id == "chennai", Assessment.hazard == "rain")
                           .order_by(Assessment.computed_at)).scalars().all()[-2:]
        assert a1.model_run_id == a2.model_run_id


async def test_stand_down_needs_two_new_model_runs_and_never_auto_closes(upstream):
    upstream.wet[hq_key("chennai")] = (6.0, 30)  # 30/51 = 58.8% -> PREPARE (72 h rule)
    await scanner.run_scan("test")
    with session_scope() as s:
        assert triggers(s, "chennai")[0].level == "PREPARE"
    upstream.wet.clear()  # new (dry) model run
    await scanner.run_scan("test")
    await scanner.run_scan("test")  # same dry run re-scanned: must not count twice
    with session_scope() as s:
        t = triggers(s, "chennai")[0]
        assert t.status == "active" and len(t.state["below_runs"]) == 1
    upstream.jitter = 0.01  # values change -> a genuinely new model run
    await scanner.run_scan("test")
    with session_scope() as s:
        t = triggers(s, "chennai")[0]
        assert t.status == "stand_down_recommended"  # AC-11: recommendation, never an automatic close


async def test_ecmwf_outage_falls_back_to_gfs_and_is_recorded(upstream):
    upstream.wet[hq_key("chennai")] = (6.0, 31)
    registry.set_chaos("ensemble:ecmwf_ifs025", True)
    try:
        res = await scanner.run_scan("test")
    finally:
        registry.set_chaos("ensemble:ecmwf_ifs025", False)
    assert res["models_used"] == ["gfs025"]
    with session_scope() as s:
        a = rain(s, "chennai")
        assert a.model == "gfs025" and a.n_members == 31
        sim = s.execute(select(SourceFetch).where(SourceFetch.simulated.is_(True))).scalars().all()
        assert sim and all(f.status == "simulated_outage" for f in sim)


async def test_all_sources_down_no_new_triggers_and_no_invented_values(upstream):
    upstream.fail = {"ensemble-api.open-meteo.com", "api.open-meteo.com", "flood-api.open-meteo.com", "sachet.ndma.gov.in"}
    res = await scanner.run_scan("test")
    assert res["districts_ok"] == 0
    with session_scope() as s:
        assert rain(s, "chennai") is None  # nothing fabricated
        assert s.execute(select(Trigger)).scalars().first() is None


async def test_official_orange_alert_floor(upstream):
    upstream.sachet_cap = cap_orange("Chennai, Tiruvallur")
    await scanner.run_scan("test")
    with session_scope() as s:
        a = rain(s, "chennai")
        assert a.base_level is None and a.level == "PREPARE"
        assert a.flags["official_color"] == "orange"
        assert rain(s, "madurai").level is None
