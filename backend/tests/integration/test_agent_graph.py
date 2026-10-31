"""Agent graph end to end with a scripted LLM double (control flow only — tests/ only).

Covers: tool loop + trace, VERIFY rejecting a non-verbatim quote and a passed phase,
retry with specific feedback, citation enrichment with real page numbers, comms
validation, CAP validity, approval gate and audit log (AC-04, AC-08, AC-09, AC-10,
AC-13), retrieval-only mode without an LLM, and a district without a plan (AC-07).
"""
import json
import re

import pymupdf as fitz
import httpx
import pytest
from sqlalchemy import select

from app import scanner
from app.agent.llm import LLMResult
from app.agent.runner import create_run, execute_run
from app.agent.schemas import COMMS_SCHEMA, CRITIC_SCHEMA, PLAN_DRAFT_SCHEMA
from app.comms.cap import validate_cap
from app.config import get_config
from app.db.models import ActionItemRow, AgentEvent, AgentRun, Approval, Communication, PublicAlert, Trigger
from app.db.session import session_scope
from app.rag.ingest import ingest_pdf
from tests.fakes import Router

PAGES = [
    "CHAPTER 4 RELIEF CAMPS\nThe Corporation shall open relief camps in schools and community halls when heavy "
    "rainfall is forecast. Relief camps shall be equipped with drinking water, food and medical kits.",
    "VULNERABLE AREAS\nLow-lying areas such as Velachery and Mudichur are prone to inundation. The Revenue "
    "Department shall alert residents of low-lying areas through public address systems.",
    "RESPONSIBILITY MATRIX\nPublic Works Department (WRD) shall monitor lake levels and regulate surplus release. "
    "Greater Chennai Corporation shall deploy dewatering pumps at subways.",
]


def make_pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def evidence_of(messages) -> dict:
    first = messages[0]["content"]
    m = re.search(r"<evidence>\n(.*?)\n</evidence>", first, re.S)
    return json.loads(m.group(1))


class ScriptedLLM:
    """Plays the model's part step by step; never used outside tests."""

    name = "scripted-test-llm"

    def __init__(self, plan_mode="normal"):
        self.events: list[str] = []
        self.gather_calls = 0
        self.plan_calls = 0
        self.plan_mode = plan_mode

    def _res(self, content, text="", tool_uses=None, parsed=None, stop="end_turn"):
        return LLMResult(content=content, text=text, tool_uses=tool_uses or [], stop_reason=stop, model=self.name,
                         input_tokens=10, output_tokens=10, thinking=["(test) reasoning summary"], parsed=parsed)

    async def chat(self, *, system, messages, tools=None, output_schema=None, max_tokens=16000, effort=None):
        if tools:
            self.gather_calls += 1
            queries = ["relief camps schools", "vulnerable low-lying areas"]
            if self.gather_calls <= len(queries):
                tu = {"id": f"tu{self.gather_calls}", "name": "retrieve_plan",
                      "input": {"query": queries[self.gather_calls - 1], "k": 3}}
                return self._res([{"type": "tool_use", **tu}], tool_uses=[tu], stop="tool_use")
            return self._res([{"type": "text", "text": "Found relief camps and vulnerable areas."}],
                             text="Found relief camps and vulnerable areas.")
        if output_schema is PLAN_DRAFT_SCHEMA:
            self.plan_calls += 1
            ev = evidence_of(messages)
            chunks = {c["page"]: c for c in ev["district_plan_chunks"]}
            h = ev["assessment"]["headline"] if ev.get("assessment") else None
            summary = (f"{h['probability_pct']}% ({h['members_exceeding']} of {h['valid_members']} forecasts) show very heavy rain."
                       if h else "No live assessment.")
            if self.plan_mode == "no_plan":
                draft = {"evidence_summary": summary, "open_questions": ["Not found in district plan: relief camps."],
                         "warnings": [], "actions": [{"id": "A1", "phase": "Do now", "action": "Activate the district control room.",
                                                      "responsible": None, "location_refs": [], "source": "suggested",
                                                      "citations": [], "rationale": "Prudent readiness step."}]}
                return self._res([{"type": "text", "text": json.dumps(draft)}], text=json.dumps(draft), parsed=draft)
            good = self.plan_calls > 1
            draft = {
                "evidence_summary": summary,
                "open_questions": ["Confirm which schools are designated as relief camps this season."],
                "warnings": [],
                "actions": [
                    {"id": "A1", "phase": "Do now", "action": "Open relief camps in schools and community halls.",
                     "responsible": "Greater Chennai Corporation", "location_refs": [], "source": "district_plan",
                     "citations": [{"chunk_id": chunks[1]["chunk_id"],
                                    "quote": ("The Corporation shall open relief camps in schools and community halls"
                                              if good else "The Corporation will open relief camps")}],
                     "rationale": "Plan requires camps when heavy rainfall is forecast."},
                    {"id": "A2", "phase": "T-24h" if good else "T-48h",
                     "action": "Alert residents of low-lying areas such as Velachery and Mudichur.",
                     "responsible": "Revenue Department", "location_refs": ["Velachery"], "source": "district_plan",
                     "citations": [{"chunk_id": chunks[2]["chunk_id"],
                                    "quote": "Low-lying areas such as Velachery and Mudichur are prone to inundation."}],
                     "rationale": "Named as prone to inundation."},
                    {"id": "A3", "phase": "Do now", "action": "Keep medical teams on standby at relief camps.",
                     "responsible": None, "location_refs": [], "source": "suggested", "citations": [],
                     "rationale": "Prudent; not explicit in plan."},
                ],
            }
            return self._res([{"type": "text", "text": json.dumps(draft)}], text=json.dumps(draft), parsed=draft)
        if output_schema is CRITIC_SCHEMA:
            v = {"verdict": "pass", "issues": [], "gaps": []}
            return self._res([{"type": "text", "text": json.dumps(v)}], text=json.dumps(v), parsed=v)
        if output_schema is COMMS_SCHEMA:
            wording = re.search(r'"((?:very likely|likely|possible) \(\d+%\))"', system).group(1)
            pct = re.search(r"\((\d+)%\)", wording).group(1)
            d = {"sms_en": f"Chennai: very heavy rain {wording} in the next 2 days. Move valuables up and avoid low-lying roads.",
                 "sms_ta": f"சென்னை: அடுத்த 2 நாட்களில் மிக கனமழை, மிக அதிக வாய்ப்பு ({pct}%). தாழ்வான சாலைகளைத் தவிர்க்கவும்.",
                 "headline_en": "Very heavy rain expected in Chennai", "description_en": "Very heavy rain is very likely.",
                 "instruction_en": "Avoid low-lying roads.", "headline_ta": "சென்னையில் மிக கனமழை",
                 "description_ta": "மிக கனமழைக்கு வாய்ப்பு.", "instruction_ta": "தாழ்வான சாலைகளைத் தவிர்க்கவும்."}
            return self._res([{"type": "text", "text": json.dumps(d, ensure_ascii=False)}], parsed=d)
        raise AssertionError("unexpected LLM call")


def hq_key(district_id):
    d = get_config().district(district_id)
    return f"{d.hq[0]:.3f},{d.hq[1]:.3f}"


@pytest.fixture
async def chennai_act(fresh_db, mock_http):
    r = Router()
    r.wet[hq_key("chennai")] = (6.0, 40)
    mock_http(r)
    await scanner.run_scan("test")
    ingest_pdf(make_pdf(PAGES), title="Chennai District Disaster Management Plan (test fixture)", kind="district",
               district_id="chennai", source_url="https://example.invalid/ddmp.pdf", edition_year=2024)
    with session_scope() as s:
        t = s.execute(select(Trigger).where(Trigger.district_id == "chennai")).scalars().first()
        return t.id


async def test_agent_run_retries_on_verify_failure_then_passes(chennai_act):
    llm = ScriptedLLM()
    run_id = create_run("chennai", "rain", trigger_id=chennai_act, started_by="test", model=llm.name)
    await execute_run(run_id, llm)
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        assert run.status == "completed", run.error
        assert run.attempts == 2
        assert run.verification["passed"] is True
        types = [e.type for e in s.execute(select(AgentEvent).where(AgentEvent.run_id == run_id)
                                           .order_by(AgentEvent.seq)).scalars()]
        assert "retry" in types and "verify" in types and "tool_call" in types and types[-1] == "done"
        retry = s.execute(select(AgentEvent).where(AgentEvent.run_id == run_id, AgentEvent.type == "retry")).scalars().first()
        assert "quote not found on p. 1" in retry.payload["message"]
        assert "phase" in retry.payload["message"] or "T-48h" in retry.payload["message"]
        acts = s.execute(select(ActionItemRow).where(ActionItemRow.run_id == run_id)
                         .order_by(ActionItemRow.position)).scalars().all()
        assert [a.source for a in acts] == ["district_plan", "district_plan", "suggested"]
        assert acts[0].citations[0]["page"] == 1 and acts[1].citations[0]["page"] == 2
        assert acts[0].citations[0]["document_title"].startswith("Chennai District")
        comms = {c.kind: c for c in s.execute(select(Communication).where(Communication.run_id == run_id)).scalars()}
        assert comms["public_en"].valid and comms["public_ta"].valid, (comms["public_en"].validation, comms["public_ta"].validation)
        assert validate_cap(comms["cap"].content)["valid"]
        assert "briefing" in comms
        assert run.plan["key_numbers"]["members_exceeding"] == 40.0


async def test_approval_gate_and_audit(chennai_act):
    from app.main import app

    llm = ScriptedLLM()
    run_id = create_run("chennai", "rain", trigger_id=chennai_act, started_by="test", model=llm.name)
    await execute_run(run_id, llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        tok = (await c.post("/api/auth/login", json={"name": "Duty Officer", "passcode": "officer-pass"})).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        run = (await c.get(f"/api/runs/{run_id}")).json()
        cap = next(x for x in run["communications"] if x["kind"] == "cap")
        # publishing without a confirmation token is impossible
        r = await c.post(f"/api/communications/{cap['id']}/approve", json={"confirmation_token": "0.bogus-token"}, headers=H)
        assert r.status_code == 400
        assert (await c.get("/api/public/alerts")).json()["alerts"] == []
        # approve one action (needs confirm)
        a0 = run["actions"][0]
        assert (await c.patch(f"/api/actions/{a0['id']}", json={"decision": "approve"}, headers=H)).status_code == 400
        r = await c.patch(f"/api/actions/{a0['id']}", json={"decision": "approve", "confirm": True}, headers=H)
        assert r.json()["status"] == "approved"
        # edit that breaks the SMS limit -> invalid -> publish blocked
        en = next(x for x in run["communications"] if x["kind"] == "public_en")
        r = await c.patch(f"/api/communications/{en['id']}", json={"content": "x" * 170}, headers=H)
        assert r.json()["valid"] is False
        conf = (await c.post(f"/api/communications/{cap['id']}/confirmation", headers=H)).json()
        r = await c.post(f"/api/communications/{cap['id']}/approve", json={"confirmation_token": conf["token"]}, headers=H)
        assert r.status_code == 422
        # fix it, confirm again, publish
        await c.patch(f"/api/communications/{en['id']}", json={"content": "Chennai: very heavy rain very likely (78%) in 2 days. Avoid low-lying roads."}, headers=H)
        conf = (await c.post(f"/api/communications/{cap['id']}/confirmation", headers=H)).json()
        r = await c.post(f"/api/communications/{cap['id']}/approve", json={"confirmation_token": conf["token"]}, headers=H)
        assert r.status_code == 200 and r.json()["published"] is True
        alerts = (await c.get("/api/public/alerts")).json()["alerts"]
        assert len(alerts) == 1 and alerts[0]["msg_type"] == "Alert"
        xml = (await c.get(alerts[0]["cap_url"]))
        assert xml.headers["x-cap-valid"] == "true" and "<status>Exercise</status>" in xml.text
        audit = (await c.get("/api/audit", headers=H)).json()["entries"]
        assert {e["decision"] for e in audit} >= {"approve", "edit", "approve_publish"}
    with session_scope() as s:
        assert s.execute(select(PublicAlert)).scalars().first() is not None
        assert len(s.execute(select(Approval)).scalars().all()) >= 4


async def test_retrieval_only_mode_without_llm(chennai_act):
    run_id = create_run("chennai", "rain", trigger_id=chennai_act, started_by="test", model=None)
    await execute_run(run_id, None)
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        assert run.status == "unavailable"
        assert run.state["retrieved_chunks"], "plan sections are still retrieved for the officer"
        assert s.execute(select(ActionItemRow).where(ActionItemRow.run_id == run_id)).scalars().first() is None


async def test_district_without_plan_is_labelled(fresh_db, mock_http):
    r = Router()
    r.wet[hq_key("madurai")] = (6.0, 40)
    mock_http(r)
    await scanner.run_scan("test")
    llm = ScriptedLLM(plan_mode="no_plan")
    run_id = create_run("madurai", "rain", trigger_id=None, started_by="test", model=llm.name)
    await execute_run(run_id, llm)
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        assert run.status == "completed"
        assert any("No district plan available" in w for w in run.plan["warnings"])
        acts = s.execute(select(ActionItemRow).where(ActionItemRow.run_id == run_id)).scalars().all()
        assert all(a.source != "district_plan" for a in acts)
