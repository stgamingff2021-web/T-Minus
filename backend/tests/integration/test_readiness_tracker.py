"""Features 3 and 6: readiness verdicts from plan evidence, officer confirmation, and the task
lifecycle (approve -> due time -> blocked needs a reason -> overdue -> reject cancels)."""
import datetime as dt

import pymupdf as fitz
from fastapi.testclient import TestClient

from app import readiness
from app.db.models import ActionItemRow, ActionTask, AgentRun, utcnow
from app.db.session import session_scope
from app.main import create_app
from app.rag.ingest import ingest_pdf

PLAN = ["CONTACTS\nR. Devi\nInspector of Police\n9443746529",
        "RELIEF CENTRES\nThere are 12 relief centres to a total capacity of 2880 in the district.\n"
        "EQUIPMENT\nBoats – 25 Nos.\nPumps – 10 Nos."]


def make_pdf(pages):
    doc = fitz.open()
    for t in pages:
        doc.new_page().insert_textbox(fitz.Rect(40, 40, 560, 800), t, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def setup_run(routes=None, edition=None):
    ingest_pdf(make_pdf(PLAN), title="Chennai DDMP", kind="district", district_id="chennai",
               source_url="https://example.gov.in/p.pdf", edition_year=edition or utcnow().year)
    t_star = utcnow() + dt.timedelta(hours=30)
    with session_scope() as s:
        s.add(AgentRun(id="run_t1", district_id="chennai", hazard="rain", status="completed",
                       state={"assessment": {"headline": {"expected_impact_utc": t_star.isoformat()}},
                              "facilities": [], "routes": routes or []}))
        s.flush()
        for i, (key, phase, text, who) in enumerate([
            ("A1", "Do now", "Open relief centres and evacuate low-lying streets", "Revenue"),
            ("A2", "T-24h", "Pre-position boats with rescue teams", "Police"),
            ("A3", "T-6h", "Run dewatering pumps at subways", "Corporation"),
        ]):
            s.add(ActionItemRow(id=f"act_{key}", run_id="run_t1", key=key, phase=phase, action=text, responsible=who,
                                source="district_plan", citations=[], position=i))
    return t_star


def by_req(result, action_id):
    return {c["requirement"]: c for c in result[action_id]["checks"]}


def test_readiness_verdicts_follow_the_evidence(fresh_db):
    setup_run()
    res = readiness.assess_run("run_t1")
    a1, a2, a3 = by_req(res, "act_A1"), by_req(res, "act_A2"), by_req(res, "act_A3")
    assert a1["shelter"]["status"] == "ready" and "2,880" in a1["shelter"]["reason"]
    assert a1["road_access"]["status"] == "unverified"  # no route checked: never "ready" by default
    assert a2["rescue_equipment"]["status"] == "ready" and "Boats 25" in a2["rescue_equipment"]["reason"]
    assert a2["staff"]["status"] == "ready"  # a Police contact is in the plan
    assert a3["pumping"]["status"] == "ready"
    assert res["act_A1"]["overall"] == "unverified"


def test_old_plan_figures_are_unverified_and_routes_make_access_ready(fresh_db):
    setup_run(routes=[{"id": "route:1", "distance_m": 4200, "duration_s": 900, "avoided": ["area:1"]}],
              edition=utcnow().year - 3)
    a1 = by_req(readiness.assess_run("run_t1"), "act_A1")
    assert a1["shelter"]["status"] == "unverified" and "over a year old" in a1["shelter"]["reason"]
    assert a1["road_access"]["status"] == "ready" and "4.2 km" in a1["road_access"]["reason"]


def login(c):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"name": "Officer A", "passcode": "officer-pass"}).json()["token"]}


def test_officer_confirmation_survives_recheck(fresh_db):
    setup_run()
    readiness.assess_run("run_t1")
    c = TestClient(create_app())
    h = login(c)
    check = by_req(c.get("/api/runs/run_t1/readiness").json()["by_action"], "act_A1")["road_access"]
    r = c.patch(f"/api/readiness/{check['id']}", json={"status": "unavailable", "note": "Bridge closed"}, headers=h)
    assert r.status_code == 200 and r.json()["verified_by"] == "Officer A"
    again = by_req(c.post("/api/runs/run_t1/readiness", headers=h).json()["by_action"], "act_A1")["road_access"]
    assert again["status"] == "unavailable" and "Bridge closed" in again["reason"]
    assert c.patch(f"/api/readiness/{check['id']}", json={"status": "ready"}).status_code == 401


def test_task_lifecycle(fresh_db):
    t_star = setup_run()
    readiness.assess_run("run_t1")
    c = TestClient(create_app())
    h = login(c)
    for key in ("A1", "A2"):
        assert c.patch(f"/api/actions/act_{key}", json={"decision": "approve", "confirm": True}, headers=h).status_code == 200
    data = c.get("/api/tasks", params={"district_id": "chennai"}).json()
    tasks = {t["key"]: t for t in data["tasks"]}
    assert set(tasks) == {"A1", "A2"} and data["counts"]["pending"] == 2
    due_a2 = dt.datetime.fromisoformat(tasks["A2"]["due_at"])
    assert abs((due_a2 - (t_star - dt.timedelta(hours=24))).total_seconds()) < 5  # T-24h -> t* - 24 h
    assert tasks["A1"]["department"] == "Revenue"

    tid = tasks["A2"]["id"]
    assert c.patch(f"/api/tasks/{tid}", json={"status": "blocked"}, headers=h).status_code == 400  # needs a reason
    r = c.patch(f"/api/tasks/{tid}", json={"status": "blocked", "blocker": "Boats under repair"}, headers=h).json()
    assert r["status"] == "blocked" and r["blocker"] == "Boats under repair"
    assert c.get("/api/tasks").json()["counts"]["problems"] >= 1

    with session_scope() as s:  # make A1 overdue
        task = s.get(ActionTask, tasks["A1"]["id"])
        task.due_at = utcnow() - dt.timedelta(minutes=5)
    overdue = c.get("/api/tasks", params={"status": "overdue"}).json()
    assert [t["key"] for t in overdue["tasks"]] == ["A1"] and overdue["counts"]["overdue"] == 1

    assert c.patch(f"/api/tasks/{tasks['A1']['id']}", json={"status": "completed"}, headers=h).json()["overdue"] is False
    c.patch("/api/actions/act_A2", json={"decision": "reject", "confirm": True}, headers=h)
    assert [t["key"] for t in c.get("/api/tasks").json()["tasks"]] == ["A1"]  # rejected -> cancelled, hidden
    assert c.patch(f"/api/tasks/{tid}", json={"status": "pending"}, headers=h).status_code == 400
    audit = c.get("/api/audit", headers=h).json()
    assert any(e.get("target_type") == "task" for e in (audit.get("entries") or audit.get("items") or []))
