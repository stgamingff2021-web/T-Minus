"""Feature 3 — can the district actually carry out each action?

Code detects what an action needs (shelter, transport, rescue equipment, earth movers, pumps,
staff, road access, backup power, medical) from the keywords in ``config/hazards.yaml``
(``readiness.requirements``) and looks for evidence in the structured plan (feature 1) and in
the OpenStreetMap facilities and routes found during the run.

* **Ready** — positive, current evidence: the plan lists it with a quantity or capacity and the
  plan is no older than ``plan_max_age_years``; a route was found; or an officer confirmed it.
* **Unavailable** — positive evidence that it is missing: the plan lists it as nil, neither the
  plan nor a successful OpenStreetMap search has any shelter, or an officer marked it so.
* **Unverified** — everything else. Missing evidence never becomes Ready, nor Unavailable.

Officer confirmations are kept when the checks are recomputed.
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import delete, select

from app.config import get_config
from app.db.models import ActionItemRow, AgentRun, ReadinessCheck, utcnow
from app.db.session import session_scope
from app.plandb.extract import department_of
from app.plandb.store import district_records, plan_age_years

STATUSES = ("ready", "unavailable", "unverified")
REQUIREMENT_LABELS = {
    "shelter": "Shelter", "transport": "Transport", "rescue_equipment": "Rescue equipment",
    "earth_moving": "Earth movers / tree cutters", "pumping": "Pumps", "staff": "Staff",
    "road_access": "Road access", "backup_power": "Backup power", "medical": "Medical",
}
RESOURCE_ITEMS = {
    "transport": ("Lorries / vehicles",),
    "rescue_equipment": ("Boats", "Life jackets", "Life buoys"),
    "earth_moving": ("Earth movers / JCB", "Tree cutters / power saws"),
    "pumping": ("Pumps",),
    "backup_power": ("Generators",),
    "medical": ("Ambulances",),
}
SHELTER_FACILITIES = {"shelter", "community_centre", "school"}
POWER_TAGS = ("generator:source", "backup_generator", "emergency_power")


def _cfg() -> dict:
    return get_config().hazards.get("readiness", {})


def detect_requirements(text: str) -> list[str]:
    out = []
    for req, words in (_cfg().get("requirements") or {}).items():
        if any(re.search(r"\b" + re.escape(w), text, re.I) for w in words):
            out.append(req)
    return out


def _ev(r: dict) -> dict:
    return {"source": "plan", "label": r["label"], "page": r["page"], "document_id": r["document_id"],
            "quote": r["quote"][:300]}


def _fresh(records: list[dict]) -> bool:
    max_age = int(_cfg().get("plan_max_age_years", 1))
    ages = [plan_age_years(r.get("edition_year")) for r in records]
    return bool(ages) and all(a is not None and a <= max_age for a in ages)


def _resource(req: str, plan: dict) -> tuple[str, str, list]:
    items = RESOURCE_ITEMS[req]
    recs = [r for r in plan["resource"] if r["fields"].get("item") in items]
    inv = [r for r in recs if r["fields"].get("context") == "inventory"]
    have = sorted([r for r in inv if (r["fields"].get("quantity") or 0) > 0],
                  key=lambda r: -(r["fields"].get("quantity") or 0))
    if have:
        listing = ", ".join(f"{r['fields']['item']} {r['fields']['quantity']} (p.{r['page']})" for r in have[:3])
        if _fresh(have):
            return "ready", f"Plan lists {listing}.", [_ev(r) for r in have[:3]]
        year = have[0].get("edition_year") or "unknown"
        return "unverified", f"Plan lists {listing}; edition {year} is over a year old.", [_ev(r) for r in have[:3]]
    nil = [r for r in inv if r["fields"].get("quantity") == 0]
    if nil:
        return "unavailable", f"Plan lists nil (p.{nil[0]['page']}).", [_ev(nil[0])]
    past = [r for r in recs if r["fields"].get("context") == "past_use"]
    if past:
        return "unverified", f"Only past use recorded (p.{past[0]['page']}).", [_ev(past[0])]
    return "unverified", "Not listed in the plan.", []


def _shelter(plan: dict, facilities: list[dict]) -> tuple[str, str, list]:
    recs = plan["shelter"]
    agg = [r for r in recs if r["fields"].get("aggregate")]
    with_cap = [r for r in recs if not r["fields"].get("aggregate") and r["fields"].get("capacity")]
    if agg or with_cap:
        if agg:
            best = max(agg, key=lambda r: r["fields"]["capacity"])
            reason = f"{best['fields']['count']} relief centres, capacity {best['fields']['capacity']:,} (p.{best['page']})."
            ev = [_ev(best)]
        else:
            total = sum(r["fields"]["capacity"] for r in with_cap)
            reason = f"{len(with_cap)} listed shelters, capacity {total:,}."
            ev = [_ev(r) for r in with_cap[:3]]
        if _fresh(agg or with_cap):
            return "ready", reason, ev
        return "unverified", reason[:-1] + "; plan edition is over a year old.", ev
    if recs:
        return "unverified", f"{len(recs)} shelters listed without capacity.", [_ev(r) for r in recs[:3]]
    osm = [f for f in facilities if f.get("type") in SHELTER_FACILITIES]
    if osm:
        return "unverified", f"{len(osm)} candidate buildings in OpenStreetMap; capacity unknown.", [
            {"source": "osm", "label": f.get("name") or f.get("type"), "osm_id": f.get("id")} for f in osm[:3]]
    if plan["structured"] and facilities:
        return "unavailable", "No shelter in the plan or the OpenStreetMap results.", []
    return "unverified", "No shelter list found.", []


def _staff(action: ActionItemRow, plan: dict) -> tuple[str, str, list]:
    dept = department_of(action.responsible or "") or department_of(action.edited_text or action.action)
    if not dept:
        return "unverified", "Responsible department not identified.", []
    contacts = [r for r in plan["contact"] if r["fields"].get("department") == dept]
    if not contacts:
        return "unverified", f"No {dept} contact in the plan.", []
    if _fresh(contacts):
        return "ready", f"{len(contacts)} {dept} contacts in the plan.", [_ev(r) for r in contacts[:3]]
    return "unverified", f"{len(contacts)} {dept} contacts in the plan; edition is over a year old.", \
        [_ev(r) for r in contacts[:3]]


def _road(routes: list[dict]) -> tuple[str, str, list]:
    if routes:
        r = routes[0]
        km = round((r.get("distance_m") or 0) / 1000, 1)
        mins = round((r.get("duration_s") or 0) / 60)
        avoided = len(r.get("avoided") or [])
        return "ready", f"Route found: {km} km, {mins} min" + (f", avoiding {avoided} flood areas." if avoided else "."), [
            {"source": "route", "label": r.get("id")}]
    return "unverified", "Route not checked.", []


def _power(action: ActionItemRow, plan: dict, facilities: list[dict]) -> tuple[str, str, list]:
    refs = set(action.location_refs or [])
    tagged = [f for f in facilities if (f.get("id") in refs or not refs) and any((f.get("tags") or {}).get(t) for t in POWER_TAGS)]
    if tagged:
        f = tagged[0]
        return "ready", f"Backup power tagged at {f.get('name') or f.get('id')} (OpenStreetMap).", [
            {"source": "osm", "label": f.get("name"), "osm_id": f.get("id")}]
    return _resource("backup_power", plan)


def _medical(plan: dict, facilities: list[dict]) -> tuple[str, str, list]:
    status, reason, ev = _resource("medical", plan)
    if status == "ready":
        return status, reason, ev
    beds = [f for f in facilities if f.get("type") == "hospital" and (f.get("tags") or {}).get("beds")]
    if beds:
        total = sum(int(re.sub(r"\D", "", f["tags"]["beds"]) or 0) for f in beds)
        return "ready", f"{len(beds)} hospitals with {total} beds tagged in OpenStreetMap.", [
            {"source": "osm", "label": f.get("name"), "osm_id": f.get("id")} for f in beds[:3]]
    return status, reason, ev


def _plan_evidence(district_id: str) -> dict:
    recs = district_records(district_id, ("shelter", "resource", "contact"))
    grouped: dict[str, list] = defaultdict(list)
    for r in recs:
        grouped[r["kind"]].append(r)
    return {"shelter": grouped["shelter"], "resource": grouped["resource"], "contact": grouped["contact"],
            "structured": bool(recs)}


def check_action(action: ActionItemRow, plan: dict, facilities: list[dict], routes: list[dict]) -> list[dict]:
    text = " ".join(x for x in (action.edited_text or action.action, action.responsible or "") if x)
    out = []
    for req in detect_requirements(text):
        if req == "shelter":
            status, reason, ev = _shelter(plan, facilities)
        elif req == "staff":
            status, reason, ev = _staff(action, plan)
        elif req == "road_access":
            status, reason, ev = _road(routes)
        elif req == "backup_power":
            status, reason, ev = _power(action, plan, facilities)
        elif req == "medical":
            status, reason, ev = _medical(plan, facilities)
        else:
            status, reason, ev = _resource(req, plan)
        out.append({"requirement": req, "status": status, "reason": reason, "evidence": ev})
    return out


def assess_run(run_id: str) -> dict[str, list[dict]]:
    """(Re)compute readiness for every action of a run; officer confirmations are kept."""
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        if run is None:
            return {}
        state = run.state or {}
        facilities, routes = state.get("facilities") or [], state.get("routes") or []
        plan = _plan_evidence(run.district_id)
        actions = list(s.execute(select(ActionItemRow).where(ActionItemRow.run_id == run_id)).scalars())
        ids = [a.id for a in actions]
        kept = {(c.action_id, c.requirement) for c in s.execute(
            select(ReadinessCheck).where(ReadinessCheck.action_id.in_(ids), ReadinessCheck.verified_by.is_not(None))).scalars()}
        s.execute(delete(ReadinessCheck).where(ReadinessCheck.action_id.in_(ids), ReadinessCheck.verified_by.is_(None)))
        for a in actions:
            for c in check_action(a, plan, facilities, routes):
                if (a.id, c["requirement"]) not in kept:
                    s.add(ReadinessCheck(action_id=a.id, checked_at=utcnow(), **c))
    return readiness_for_run(run_id)


def check_dict(c: ReadinessCheck) -> dict:
    return {"id": c.id, "requirement": c.requirement, "label": REQUIREMENT_LABELS.get(c.requirement, c.requirement),
            "status": c.status, "reason": c.reason, "evidence": c.evidence or [],
            "verified_by": c.verified_by, "verified_at": c.verified_at.isoformat() if c.verified_at else None}


def overall(checks: list[dict]) -> str | None:
    if not checks:
        return None
    statuses = {c["status"] for c in checks}
    return "unavailable" if "unavailable" in statuses else "unverified" if "unverified" in statuses else "ready"


def readiness_for_actions(action_ids: list[str]) -> dict[str, dict]:
    if not action_ids:
        return {}
    with session_scope() as s:
        rows = s.execute(select(ReadinessCheck).where(ReadinessCheck.action_id.in_(action_ids))
                         .order_by(ReadinessCheck.id)).scalars()
        by: dict[str, list] = defaultdict(list)
        for c in rows:
            by[c.action_id].append(check_dict(c))
    return {a: {"overall": overall(v), "checks": v} for a, v in by.items()}


def readiness_for_run(run_id: str) -> dict[str, dict]:
    with session_scope() as s:
        ids = [a for (a,) in s.execute(select(ActionItemRow.id).where(ActionItemRow.run_id == run_id))]
    return readiness_for_actions(ids)


def confirm(check_id: int, status: str, note: str | None, user_name: str) -> dict | None:
    with session_scope() as s:
        c = s.get(ReadinessCheck, check_id)
        if c is None:
            return None
        before = f"{c.status}: {c.reason}"
        c.status = status
        c.reason = f"Confirmed by {user_name}" + (f": {note}" if note else ".")
        c.verified_by, c.verified_at = user_name, utcnow()
        from app.db.models import Approval

        s.add(Approval(target_type="readiness", target_id=str(c.id), decision=f"mark_{status}", before=before,
                       after=c.reason, user=user_name, role="officer", note=note))
        return check_dict(c)
