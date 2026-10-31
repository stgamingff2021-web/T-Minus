"""Agent runs, live trace (SSE), approvals, communications and the public page."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import notify
from app.agent.runner import build_run_cap, start_run_background
from app.api.common import iso, run_brief
from app.auth import User, current_user
from app.comms import sms
from app.comms.briefing import render_html, render_pdf
from app.config import get_config, get_settings
from app.db.models import (
    ActionItemRow,
    AgentEvent,
    AgentRun,
    Approval,
    Communication,
    District,
    PublicAlert,
    Trigger,
    utcnow,
)
from app.db.session import session_scope
from app.errors import APIError
from app.ratelimit import limit

router = APIRouter(prefix="/api", tags=["runs"])
MAX_TEXT = 1000
_HTML_TAG = re.compile(r"<[^>]+>")


class PlanBody(BaseModel):
    hazard: Literal["rain", "river", "heat"] = "rain"


@router.post("/districts/{district_id}/plan", status_code=202, dependencies=[Depends(limit(capacity=6, refill_per_s=1 / 30))])
async def start_plan(district_id: str, body: PlanBody, user: User = Depends(current_user)):
    if get_config().district(district_id) is None:
        raise APIError(404, "unknown_district", f"Unknown district '{district_id}'.")
    with session_scope() as s:
        trig = s.execute(select(Trigger).where(Trigger.district_id == district_id, Trigger.hazard == body.hazard,
                                               Trigger.status != "closed").order_by(Trigger.opened_at.desc())).scalars().first()
        trigger_id = trig.id if trig else None
    run_id = start_run_background(district_id, body.hazard, trigger_id=trigger_id, started_by=user.name)
    return {"run_id": run_id, "trigger_id": trigger_id}


@router.get("/runs")
def list_runs(district_id: str | None = None, limit_n: int = Query(30, alias="limit", le=200)):
    with session_scope() as s:
        q = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit_n)
        if district_id:
            q = q.where(AgentRun.district_id == district_id)
        return {"runs": [run_brief(r) for r in s.execute(q).scalars()]}


def _action_dict(a: ActionItemRow) -> dict:
    return {"id": a.id, "key": a.key, "phase": a.phase, "action": a.action, "edited_text": a.edited_text,
            "responsible": a.responsible, "location_refs": a.location_refs, "source": a.source,
            "citations": a.citations, "rationale": a.rationale, "status": a.status, "problems": a.problems}


def _comm_dict(c: Communication) -> dict:
    return {"id": c.id, "kind": c.kind, "content": c.content if c.kind != "briefing" else None, "valid": c.valid,
            "validation": c.validation, "status": c.status, "updated_at": iso(c.updated_at)}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    with session_scope() as s:
        r = s.get(AgentRun, run_id)
        if r is None:
            raise APIError(404, "not_found", "Run not found")
        actions = s.execute(select(ActionItemRow).where(ActionItemRow.run_id == run_id)
                            .order_by(ActionItemRow.position)).scalars().all()
        comms = s.execute(select(Communication).where(Communication.run_id == run_id)).scalars().all()
        d = s.get(District, r.district_id)
        trig = s.get(Trigger, r.trigger_id) if r.trigger_id else None
        published = s.execute(select(PublicAlert).where(PublicAlert.run_id == run_id)).scalars().all()
        return {
            "run": {**run_brief(r), "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "error": r.error},
            "district": {"id": d.id, "name": d.name, "state": d.state, "hq": [d.centroid_lat, d.centroid_lon],
                         "geometry": d.boundary} if d else None,
            "trigger": {"id": trig.id, "level": trig.level, "status": trig.status, "t_star": iso(trig.t_star),
                        "cap_msg_type": trig.cap_msg_type} if trig else None,
            "plan": r.plan, "verification": r.verification, "state": r.state or {},
            "actions": [_action_dict(a) for a in actions],
            "communications": [_comm_dict(c) for c in comms],
            "published": [{"id": p.id, "published_at": iso(p.published_at), "msg_type": p.msg_type} for p in published],
        }


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, last_event_id: str | None = Header(default=None),
                     after: int = 0):
    """SSE stream of the agent trace; resumes from Last-Event-ID (§8.4)."""
    with session_scope() as s:
        if s.get(AgentRun, run_id) is None:
            raise APIError(404, "not_found", "Run not found")
    start_after = int(last_event_id) if (last_event_id or "").isdigit() else after
    q = notify.subscribe(f"run:{run_id}")

    async def gen():
        sent = start_after
        try:
            with session_scope() as s:
                rows = s.execute(select(AgentEvent).where(AgentEvent.run_id == run_id, AgentEvent.seq > sent)
                                 .order_by(AgentEvent.seq)).scalars().all()
                backlog = [{"seq": e.seq, "ts": iso(e.ts), "type": e.type, "payload": e.payload} for e in rows]
                status = s.get(AgentRun, run_id).status
            for ev in backlog:
                sent = ev["seq"]
                yield f"id: {ev['seq']}\nevent: trace\ndata: {json.dumps(ev, default=str)}\n\n"
            if status != "running" and any(ev["type"] == "done" for ev in backlog) or (status != "running" and not backlog and sent > 0):
                yield "event: end\ndata: {}\n\n"
                return
            last_ping = time.monotonic()
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=5)
                except asyncio.TimeoutError:
                    if time.monotonic() - last_ping > 15:
                        last_ping = time.monotonic()
                        yield ": ping\n\n"
                    with session_scope() as s:
                        if s.get(AgentRun, run_id).status != "running":
                            yield "event: end\ndata: {}\n\n"
                            return
                    continue
                if ev["seq"] <= sent:
                    continue
                sent = ev["seq"]
                yield f"id: {ev['seq']}\nevent: trace\ndata: {json.dumps(ev, default=str)}\n\n"
                if ev["type"] == "done":
                    yield "event: end\ndata: {}\n\n"
                    return
        finally:
            notify.unsubscribe(f"run:{run_id}", q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

def clean_text(text: str, max_len: int = MAX_TEXT) -> str:
    t = (text or "").strip()
    if not t:
        raise APIError(422, "empty_text", "Text cannot be empty.")
    if len(t) > max_len:
        raise APIError(422, "text_too_long", f"Text is {len(t)} characters; the limit is {max_len}.")
    if _HTML_TAG.search(t):
        raise APIError(422, "html_not_allowed", "HTML is not allowed in edits.")
    return t


class ActionDecision(BaseModel):
    decision: Literal["approve", "edit", "reject", "reset"]
    text: str | None = Field(None, max_length=2000)
    confirm: bool = False
    note: str | None = Field(None, max_length=500)


@router.patch("/actions/{action_id}", dependencies=[Depends(limit(capacity=60, refill_per_s=1))])
def decide_action(action_id: str, body: ActionDecision, user: User = Depends(current_user)):
    if body.decision in ("approve", "reject") and not body.confirm:
        raise APIError(400, "confirmation_required", "Approve/reject requires explicit confirmation.")
    with session_scope() as s:
        a = s.get(ActionItemRow, action_id)
        if a is None:
            raise APIError(404, "not_found", "Action not found")
        before = a.edited_text or a.action
        if body.decision == "edit":
            a.edited_text = clean_text(body.text or "")
            a.status = "edited"
        elif body.decision == "approve":
            if body.text:
                a.edited_text = clean_text(body.text)
            a.status = "approved"
        elif body.decision == "reject":
            a.status = "rejected"
        else:
            a.status, a.edited_text = "proposed", None
        s.add(Approval(target_type="action", target_id=a.id, decision=body.decision, before=before,
                       after=a.edited_text or a.action, user=user.name, role=user.role, note=body.note))
        return _action_dict(a)


class CommEdit(BaseModel):
    content: str = Field(..., max_length=4000)
    note: str | None = Field(None, max_length=500)


def _validate_comm(kind: str, content: str) -> tuple[bool, dict]:
    pm = get_config().hazards.get("public_message", {})
    if kind == "public_en":
        problems = sms.check_english(content, int(pm.get("english_max_chars", 160)))
    elif kind == "public_ta":
        problems = sms.check_tamil(content, int(pm.get("tamil_max_chars", 134)))
    else:
        problems = []
    return not problems, {"problems": problems, **sms.count(content).as_dict()}


@router.patch("/communications/{comm_id}", dependencies=[Depends(limit(capacity=60, refill_per_s=1))])
def edit_comm(comm_id: str, body: CommEdit, user: User = Depends(current_user)):
    with session_scope() as s:
        c = s.get(Communication, comm_id)
        if c is None:
            raise APIError(404, "not_found", "Communication not found")
        if c.kind not in ("public_en", "public_ta"):
            raise APIError(400, "not_editable", "Only public messages are edited directly; CAP is rebuilt from them.")
        if c.status == "published":
            raise APIError(409, "already_published", "This message is already published.")
        text = clean_text(body.content, 400)
        before = c.content
        c.content = text
        c.valid, c.validation = _validate_comm(c.kind, text)
        c.status, c.updated_at = "proposed", utcnow()
        s.add(Approval(target_type="communication", target_id=c.id, decision="edit", before=before, after=text,
                       user=user.name, role=user.role, note=body.note))
        return _comm_dict(c)


def _confirm_token(comm_id: str, digest: str, exp: int) -> str:
    key = get_settings().secret_key.encode()
    msg = f"{comm_id}|{digest}|{exp}".encode()
    sig = base64.urlsafe_b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode().rstrip("=")
    return f"{exp}.{sig}"


def _bundle(s, run_id: str) -> dict[str, Communication]:
    return {c.kind: c for c in s.execute(select(Communication).where(Communication.run_id == run_id)).scalars()}


def _bundle_digest(b: dict[str, Communication]) -> str:
    h = hashlib.sha256()
    for k in ("public_en", "public_ta"):
        h.update((b[k].content if k in b else "").encode())
    return h.hexdigest()


@router.post("/communications/{comm_id}/confirmation", dependencies=[Depends(limit())])
def request_confirmation(comm_id: str, user: User = Depends(current_user)):
    """Step 1 of Approve & publish: returns exactly what will be published + a 5-minute token."""
    with session_scope() as s:
        c = s.get(Communication, comm_id)
        if c is None:
            raise APIError(404, "not_found", "Communication not found")
        b = _bundle(s, c.run_id)
        if "public_en" not in b or "public_ta" not in b:
            raise APIError(409, "nothing_to_publish", "This run has no public message to publish.")
        exp = int(time.time()) + 300
        return {
            "token": _confirm_token(comm_id, _bundle_digest(b), exp), "expires_in_s": 300,
            "will_publish": {"public_en": b["public_en"].content, "public_ta": b["public_ta"].content},
            "valid": b["public_en"].valid and b["public_ta"].valid,
            "problems": (b["public_en"].validation or {}).get("problems", []) + (b["public_ta"].validation or {}).get("problems", []),
            "notice": "Exercise only: posts to the T-Minus public alerts page (CAP status=Exercise). "
                      "Never sent to SMS gateways, SACHET or any government system.",
        }


class ApproveBody(BaseModel):
    confirmation_token: str = Field(..., min_length=10, max_length=200)
    note: str | None = Field(None, max_length=500)


@router.post("/communications/{comm_id}/approve", dependencies=[Depends(limit())])
def approve_publish(comm_id: str, body: ApproveBody, user: User = Depends(current_user)):
    """Step 2: Approve & publish the EN + TA messages and the CAP rebuilt from them."""
    with session_scope() as s:
        c = s.get(Communication, comm_id)
        if c is None:
            raise APIError(404, "not_found", "Communication not found")
        run = s.get(AgentRun, c.run_id)
        b = _bundle(s, c.run_id)
        if "public_en" not in b or "public_ta" not in b:
            raise APIError(409, "nothing_to_publish", "This run has no public message to publish.")
        try:
            exp_s, _ = body.confirmation_token.split(".", 1)
            exp = int(exp_s)
        except ValueError as exc:
            raise APIError(400, "bad_token", "Malformed confirmation token.") from exc
        if exp < time.time():
            raise APIError(400, "token_expired", "Confirmation expired — confirm again.")
        if not hmac.compare_digest(body.confirmation_token, _confirm_token(comm_id, _bundle_digest(b), exp)):
            raise APIError(400, "bad_token", "Confirmation does not match the current message text — review and confirm again.")
        for k in ("public_en", "public_ta"):
            ok, val = _validate_comm(k, b[k].content)
            b[k].valid, b[k].validation = ok, val
            if not ok:
                raise APIError(422, "invalid_message", f"{k}: " + "; ".join(val["problems"]) + " — publishing is blocked until fixed.")
        if any(x.status == "published" for x in b.values() if x.kind in ("public_en", "public_ta")):
            raise APIError(409, "already_published", "Already published.")
        trig = s.get(Trigger, run.trigger_id) if run.trigger_id else None
        prev = s.execute(select(PublicAlert).where(PublicAlert.district_id == run.district_id,
                                                   PublicAlert.hazard == run.hazard)
                         .order_by(PublicAlert.published_at.desc())).scalars().first()
        msg_type = "Update" if (prev and trig and trig.status != "closed" and prev.published_at >= trig.opened_at) else "Alert"
        fields = ((b.get("cap").validation or {}).get("fields") if b.get("cap") else {}) or {}
        fields = {**fields, "description_en": fields.get("description_en") or b["public_en"].content,
                  "description_ta": fields.get("description_ta") or b["public_ta"].content,
                  "instruction_en": b["public_en"].content, "instruction_ta": b["public_ta"].content}
        refs = None
        if msg_type == "Update" and prev and prev.cap_communication_id:
            pc = s.get(Communication, prev.cap_communication_id)
            refs = [_cap_ref(pc.content)] if pc else None
        xml, val = build_run_cap(run.id, run.district_id, run.hazard, (run.state or {}).get("assessment"), fields,
                                 msg_type, s.get(District, run.district_id), references=[r for r in (refs or []) if r])
        if not val["valid"]:
            raise APIError(500, "cap_invalid", "Generated CAP failed XSD validation: " + "; ".join(val["errors"][:3]))
        cap = b.get("cap")
        if cap is None:
            cap = Communication(run_id=run.id, kind="cap", content=xml)
            s.add(cap)
        before_cap = cap.content
        cap.content, cap.valid, cap.validation = xml, True, {**val, "fields": fields, "msg_type": msg_type}
        for x in (b["public_en"], b["public_ta"], cap):
            x.status, x.updated_at = "published", utcnow()
        s.flush()
        pa = PublicAlert(run_id=run.id, district_id=run.district_id, hazard=run.hazard,
                         level=(run.plan or {}).get("level") or "QUIET", msg_type=msg_type,
                         message_en=b["public_en"].content, message_ta=b["public_ta"].content,
                         cap_communication_id=cap.id, published_by=user.name,
                         data_timestamps=(run.plan or {}).get("data_timestamps") or {})
        s.add(pa)
        for k in ("public_en", "public_ta"):
            s.add(Approval(target_type="communication", target_id=b[k].id, decision="approve_publish",
                           before=None, after=b[k].content, user=user.name, role=user.role, note=body.note))
        s.add(Approval(target_type="cap", target_id=cap.id, decision="approve_publish", before=before_cap[:2000],
                       after=xml[:4000], user=user.name, role=user.role, note=f"msgType={msg_type}"))
        s.flush()
        notify.publish("system", {"type": "published", "district_id": run.district_id, "public_alert_id": pa.id})
        return {"published": True, "public_alert_id": pa.id, "msg_type": msg_type, "cap_valid": True}


def _cap_ref(xml: str) -> str | None:
    """CAP references entry 'sender,identifier,sent' of an earlier message."""
    def grab(tag: str) -> str | None:
        m = re.search(rf"<{tag}>([^<]+)</{tag}>", xml or "")
        return m.group(1) if m else None
    parts = [grab("sender"), grab("identifier"), grab("sent")]
    return ",".join(parts) if all(parts) else None


@router.post("/communications/{comm_id}/reject", dependencies=[Depends(limit())])
def reject_comm(comm_id: str, user: User = Depends(current_user)):
    with session_scope() as s:
        c = s.get(Communication, comm_id)
        if c is None:
            raise APIError(404, "not_found", "Communication not found")
        if c.status == "published":
            raise APIError(409, "already_published", "Already published.")
        c.status, c.updated_at = "rejected", utcnow()
        s.add(Approval(target_type="communication", target_id=c.id, decision="reject", before=c.content, after=None,
                       user=user.name, role=user.role))
        return _comm_dict(c)


@router.get("/communications/{comm_id}/cap.xml")
def cap_xml(comm_id: str):
    from app.comms.cap import validate_cap

    with session_scope() as s:
        c = s.get(Communication, comm_id)
        if c is None or c.kind != "cap":
            raise APIError(404, "not_found", "CAP message not found")
        val = validate_cap(c.content)
        return Response(c.content, media_type="application/xml",
                        headers={"X-CAP-Valid": str(val["valid"]).lower(), "X-CAP-Status": c.status,
                                 "Content-Disposition": f'inline; filename="{comm_id}.xml"'})


def _briefing_html(run_id: str) -> str:
    cfg = get_config()
    with session_scope() as s:
        r = s.get(AgentRun, run_id)
        if r is None:
            raise APIError(404, "not_found", "Run not found")
        d = cfg.district(r.district_id)
        actions = s.execute(select(ActionItemRow).where(ActionItemRow.run_id == run_id)
                            .order_by(ActionItemRow.position)).scalars().all()
        b = _bundle(s, run_id)
        return render_html(
            district=d.name if d else r.district_id, state=cfg.state_name(d.state) if d else "",
            plan=r.plan or {"level": "QUIET", "hazard": r.hazard, "evidence_summary": "No plan was produced.",
                            "warnings": [r.error or "Run did not complete."]},
            assessment=(r.state or {}).get("assessment"),
            actions=[_action_dict(a) for a in actions],
            comms={"public_en": b["public_en"].content, "public_ta": b["public_ta"].content}
            if "public_en" in b and "public_ta" in b else None,
            run_meta={"run_id": r.id, "model": r.model, "status": r.status},
        )


@router.get("/runs/{run_id}/briefing.html", response_class=HTMLResponse)
def briefing_html(run_id: str):
    return HTMLResponse(_briefing_html(run_id))


@router.get("/runs/{run_id}/briefing.pdf")
def briefing_pdf(run_id: str):
    html_text = _briefing_html(run_id)
    try:
        pdf = render_pdf(html_text)
    except Exception as exc:  # noqa: BLE001 - e.g. missing system fonts/libraries
        raise APIError(503, "pdf_unavailable", f"PDF rendering failed ({type(exc).__name__}); the HTML briefing is "
                                               f"available at /api/runs/{run_id}/briefing.html") from exc
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="t-minus-briefing-{run_id}.pdf"'})


@router.get("/public/alerts")
def public_alerts():
    cfg = get_config()
    with session_scope() as s:
        rows = s.execute(select(PublicAlert).order_by(PublicAlert.published_at.desc()).limit(100)).scalars().all()
        out = []
        for p in rows:
            d = cfg.district(p.district_id)
            out.append({"id": p.id, "district_id": p.district_id, "district": d.name if d else p.district_id,
                        "hazard": p.hazard, "level": p.level, "msg_type": p.msg_type, "message_en": p.message_en,
                        "message_ta": p.message_ta, "published_at": iso(p.published_at), "published_by": p.published_by,
                        "cap_url": f"/api/communications/{p.cap_communication_id}/cap.xml" if p.cap_communication_id else None,
                        "data_timestamps": p.data_timestamps})
        return {"alerts": out,
                "notice": "PROTOTYPE EXERCISE — officer-approved T-Minus outputs. Not an official warning source; "
                          "follow IMD/NDMA/district authorities."}


@router.get("/audit")
def audit(user: User = Depends(current_user), limit_n: int = Query(200, alias="limit", le=1000)):
    with session_scope() as s:
        rows = s.execute(select(Approval).order_by(Approval.ts.desc()).limit(limit_n)).scalars().all()
        return {"entries": [{"id": a.id, "ts": iso(a.ts), "user": a.user, "role": a.role, "target_type": a.target_type,
                             "target_id": a.target_id, "decision": a.decision, "before": a.before, "after": a.after,
                             "note": a.note} for a in rows]}


def escape(x: str) -> str:
    return html.escape(x or "")
