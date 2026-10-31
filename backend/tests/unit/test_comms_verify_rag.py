"""Unit tests: SMS counter, probability wording, CAP builder, VERIFY hard checks,
chunker, SACHET parsing/matching, and replay's no-look-ahead series selection."""
import datetime as dt

import numpy as np
import pytest

from app.agent.schemas import allowed_phases
from app.agent.verify import VerifyContext, evidence_numbers, quote_in_chunk, verify_plan
from app.comms import sms
from app.comms.cap import Area, InfoBlock, build_cap, certainty, severity, urgency, validate_cap
from app.comms.messages import pct_int, probability_wording, wording_with_number
from app.config import get_config
from app.rag.chunker import chunk_pages, is_heading
from app.replay.runner import as_issued_series
from app.sources.sachet import alert_covers_district, detect_color, parse_cap, parse_feed

# ---------------- SMS ----------------

def test_gsm7_single_and_concatenated():
    assert sms.count("a" * 160).segments == 1
    c = sms.count("a" * 161)
    assert c.encoding == "GSM-7" and c.segments == 2  # 153 per segment when concatenated


def test_gsm7_extension_chars_cost_two():
    assert sms.count("[]").units == 4


def test_ucs2_tamil_two_segments_limit():
    ta = "மழை" * 44  # 132 UTF-16 units
    c = sms.count(ta)
    assert c.encoding == "UCS-2" and c.units == 132 and c.segments == 2
    assert sms.check_tamil(ta) == []
    assert sms.check_tamil("மழை" * 46)  # 138 > 134


def test_english_non_gsm_char_is_flagged():
    problems = sms.check_english("Rain “very likely” today")
    assert any("non-GSM" in p for p in problems)


# ---------------- wording ----------------

@pytest.mark.parametrize("p,phrase", [(0.70, "very likely"), (0.745, "very likely"), (0.51, "likely"),
                                      (0.50, "possible"), (0.30, "possible"), (0.29, None)])
def test_probability_wording(p, phrase):
    assert probability_wording(p) == phrase


def test_wording_uses_round_half_up():
    assert pct_int(0.745) == 75
    assert wording_with_number(38 / 51) == "very likely (75%)"


# ---------------- CAP ----------------

def test_cap_mappings():
    assert urgency(31) == "Future" and urgency(0.5) == "Expected" and urgency(-1) == "Immediate"
    assert severity("heavy") == "Moderate" and severity("very_heavy") == "Severe" and severity("extremely_heavy") == "Extreme"
    assert certainty(0.51) == "Likely" and certainty(0.5) == "Possible"


def test_cap_builds_valid_exercise_xml_with_two_languages():
    now = dt.datetime(2026, 10, 31, 9, 0, tzinfo=dt.timezone.utc)
    info = dict(event="Very heavy rainfall", urgency="Future", severity="Severe", certainty="Likely",
                onset=now + dt.timedelta(hours=22), expires=now + dt.timedelta(hours=55), effective=now)
    xml = build_cap(identifier="TMINUS chennai rain,1", sender="tminus-prototype", sent=now, msg_type="Update",
                    infos=[InfoBlock("en-IN", headline="h", description="d", instruction="i", **info),
                           InfoBlock("ta-IN", headline="த", description="வி", instruction="அ", **info)],
                    area=Area("Chennai district", polygon="13.0,80.1 13.2,80.1 13.2,80.3 13.0,80.1"),
                    references=["tminus-prototype,TMINUS-1,2026-10-31T10:00:00+05:30"])
    v = validate_cap(xml)
    assert v["valid"], v["errors"]
    assert "<status>Exercise</status>" in xml and "<msgType>Update</msgType>" in xml
    assert "TMINUS-chennai-rain-1" in xml  # spaces/commas removed from identifier
    assert "+05:30" in xml and "Z<" not in xml


def test_cap_rejects_actual_status():
    xml = build_cap(identifier="x", sender="y", sent=dt.datetime.now(dt.timezone.utc), msg_type="Alert", infos=[],
                    area=Area("A")).replace("Exercise", "Actual")
    assert not validate_cap(xml)["valid"]


# ---------------- VERIFY ----------------

CHUNK = {"chunk_id": "doc1:p117:c3", "document_id": "doc1", "document_title": "DDMP", "page": 117,
         "text": "The Revenue Department shall open relief camps in\nschools and community halls  when red alert is issued."}


def ctx(t_minus=31.0, nums=(74.5, 38, 51)):
    return VerifyContext(chunks={CHUNK["chunk_id"]: CHUNK}, generic_chunks={},
                         allowed_numbers=evidence_numbers(CHUNK["text"], extra=list(nums)),
                         osm_ids={"osm:node/1"}, osm_names=["Corporation School Velachery"], geocode_ids=set(),
                         geocode_names=[], t_minus_h=t_minus)


def action(**kw):
    base = {"id": "A1", "phase": "Do now", "action": "Open relief camps.", "responsible": "Revenue Department",
            "location_refs": [], "source": "district_plan", "rationale": "Plan says so.",
            "citations": [{"chunk_id": CHUNK["chunk_id"], "quote": "shall open relief camps in schools and community halls"}]}
    base.update(kw)
    return base


def plan(*actions, summary="74.5% (38 of 51 forecasts) show very heavy rain."):
    return {"evidence_summary": summary, "actions": list(actions), "open_questions": [], "warnings": []}


def test_quote_must_be_exact_substring_whitespace_insensitive():
    assert quote_in_chunk("relief camps in schools and community halls", CHUNK["text"])
    assert not quote_in_chunk("relief camps at schools", CHUNK["text"])


def test_verify_passes_a_grounded_plan():
    assert verify_plan(plan(action()), ctx()).passed


def test_verify_rejects_paraphrased_quote_with_page_number():
    r = verify_plan(plan(action(citations=[{"chunk_id": CHUNK["chunk_id"], "quote": "will open camps"}])), ctx())
    assert not r.passed and "A1: quote not found on p. 117" in r.feedback()


def test_verify_rejects_citing_unretrieved_chunk():
    r = verify_plan(plan(action(citations=[{"chunk_id": "doc9:p1:c1", "quote": "x"}])), ctx())
    assert any(p.check == "citation" and "did not return" in p.message for p in r.problems)


def test_verify_requires_citation_for_plan_actions_but_not_suggested():
    assert not verify_plan(plan(action(citations=[])), ctx()).passed
    assert verify_plan(plan(action(source="suggested", citations=[])), ctx()).passed


def test_verify_rejects_invented_numbers_with_tolerance_for_rounding():
    assert verify_plan(plan(action(), summary="74% (38 of 51) ..."), ctx()).passed  # 74 within 0.5 of 74.5
    r = verify_plan(plan(action(action="Deploy 25 boats.")), ctx())
    assert any(p.check == "number" for p in r.problems)


def test_verify_rejects_unknown_facilities():
    r = verify_plan(plan(action(location_refs=["osm:node/999"])), ctx())
    assert any(p.check == "facility" for p in r.problems)
    r2 = verify_plan(plan(action(action="Open Saint Imaginary Higher Secondary School as a camp.")), ctx())
    assert any(p.check == "facility" for p in r2.problems)
    assert verify_plan(plan(action(location_refs=["osm:node/1"])), ctx()).passed


def test_verify_rejects_passed_phase():
    r = verify_plan(plan(action(phase="T-48h")), ctx(t_minus=31))
    assert any(p.check == "phase" for p in r.problems)
    assert allowed_phases(31) == ["Do now", "T-24h", "T-6h"]
    assert allowed_phases(5) == ["Do now"]


# ---------------- chunker ----------------

def test_chunks_never_cross_pages_and_keep_sections():
    pages = [(1, "CHAPTER 3 RELIEF CAMPS\nCamps shall open in schools.\n" + "Food and water. " * 30),
             (2, "Camps continue on page two.\n3.2 Evacuation\nEvacuate low-lying areas.")]
    chunks = chunk_pages(pages, max_chars=400)
    assert {c.page for c in chunks} == {1, 2}
    assert chunks[0].section.startswith("CHAPTER 3")
    p2 = [c for c in chunks if c.page == 2]
    assert p2[0].section.startswith("CHAPTER 3")  # carried across pages
    assert any((c.section or "").startswith("3.2") for c in p2)
    assert all(len(c.text) <= 600 for c in chunks)


def test_heading_detection():
    assert is_heading("4.2 Relief Camps")
    assert is_heading("VULNERABLE AREAS")
    assert not is_heading("The Collector shall convene the DDMA meeting.")


# ---------------- SACHET ----------------

CAP_XML = """<?xml version="1.0"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>A1</identifier>
<sender>imd</sender><sent>2026-10-31T06:00:00+05:30</sent><status>Actual</status><msgType>Alert</msgType><scope>Public</scope>
<info><event>Heavy Rain</event><urgency>Expected</urgency><severity>Severe</severity><certainty>Likely</certainty>
<expires>2026-11-01T06:00:00+05:30</expires><headline>Heavy rain warning</headline>
<area><areaDesc>Kanchipuram, Chengalpattu</areaDesc></area></info></alert>"""


def test_parse_cap_and_match_aliases():
    a = parse_cap(CAP_XML)[0]
    assert a.color == "orange" and "rain" in a.hazards()
    cfg = get_config()
    assert alert_covers_district(a, cfg.district("kancheepuram"), "Tamil Nadu")  # alias "Kanchipuram"
    assert alert_covers_district(a, cfg.district("chengalpattu"), "Tamil Nadu")
    assert not alert_covers_district(a, cfg.district("chennai"), "Tamil Nadu")


def test_explicit_colour_word_wins():
    assert detect_color("Red alert issued", severity="Moderate") == "red"
    assert detect_color("no colour here", severity="Extreme") == "red"
    assert detect_color(None, severity="Minor") is None


def test_parse_rss_and_atom():
    rss = '<rss><channel><item><title>T</title><link>https://x/cap1</link><pubDate>Sat, 31 Oct 2026 06:00:00 +0000</pubDate></item></channel></rss>'
    atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link href="https://x/cap2"/><updated>2026-10-31T06:00:00Z</updated></entry></feed>'
    assert parse_feed(rss)[0].link == "https://x/cap1"
    assert parse_feed(atom)[0].link == "https://x/cap2"


# ---------------- replay ----------------

def test_as_issued_series_never_uses_runs_after_the_replay_clock():
    H = 3600
    t0 = 1_700_000_000 // H * H
    times = [t0 + i * H for i in range(96)]
    # each previous_dayN column carries the value N so we can see which one was used
    hourly = {"time": times, **{f"precipitation_previous_day{n}": [float(n)] * 96 for n in range(1, 8)}}
    as_of = t0 + 24 * H
    valid = np.array([as_of - 5 * H, as_of + 1 * H, as_of + 24 * H, as_of + 25 * H, as_of + 49 * H])
    out = as_issued_series(hourly, valid, as_of)
    # past and first day -> day1; (24, 48] h ahead -> day2; (48, 72] -> day3
    assert list(out) == [1.0, 1.0, 1.0, 2.0, 3.0]
    # the run that produced previous_dayN at v was issued at least N*24 h before v, i.e. <= as_of
    for v, n in zip(valid, out):
        assert v - n * 24 * H <= as_of


def test_location_names_match_across_pdf_line_breaks():
    """Regression from a live run: the GCC plan prints 'Thiru – vi – ka\n-nagar zone'."""
    chunk = {**CHUNK, "chunk_id": "doc2:p432:c1", "page": 432,
             "text": "There are 3 subways located at Ganesapuram and 2 at Perambur in Thiru – vi – ka\n-nagar zone."}
    c = ctx()
    c.chunks = {chunk["chunk_id"]: chunk}
    a = action(location_refs=["Thiru – vi – ka -nagar"], citations=[{"chunk_id": chunk["chunk_id"],
                "quote": "There are 3 subways located at Ganesapuram and 2 at Perambur"}])
    r = verify_plan(plan(a), c)
    assert not [p for p in r.problems if p.check == "facility"], r.feedback()
