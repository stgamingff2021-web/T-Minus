"""Health, sources panel, chaos switch, login and the system notification stream."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import notify, scanner, scheduler
from app.api.common import iso, last_scan
from app.auth import User, admin_user, current_user, issue_token, login
from app.config import get_settings
from app.db.models import Approval, PlanDocument, ScanRun
from app.db.session import session_scope
from app.errors import APIError
from app.geo import load_boundaries
from app.rag import embed
from app.ratelimit import limit
from app.sources import registry

router = APIRouter(prefix="/api", tags=["system"])
VERSION = "1.0.0"


@router.get("/health")
def health():
    st = scanner.scan_state()
    sch = scheduler.status()
    with session_scope() as s:
        ls = last_scan(s)
        n_docs = s.execute(select(func.count(PlanDocument.id))).scalar() or 0
        n_scans = s.execute(select(func.count(ScanRun.id))).scalar() or 0
    boundaries, prov = load_boundaries()
    from app.agent.llm import llm_configured

    degraded = []
    if not sch.get("running") and get_settings().scheduler_enabled:
        degraded.append("scheduler not running")
    if ls is not None and ls.status == "failed":
        degraded.append("last scan failed")
    return {
        "status": "degraded" if degraded else "ok",
        "degraded_reasons": degraded,
        "version": VERSION,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scheduler": sch,
        "scan_running": st.get("running"),
        "last_scan": {"id": ls.id, "started_at": iso(ls.started_at), "finished_at": iso(ls.finished_at),
                      "status": ls.status, "trigger": ls.trigger,
                      "duration_s": (ls.summary or {}).get("duration_s"),
                      "districts_ok": (ls.summary or {}).get("districts_ok"),
                      "districts_failed": len((ls.summary or {}).get("districts_failed") or [])} if ls else None,
        "scans_total": n_scans,
        "llm_configured": llm_configured(),
        "llm_model": get_settings().llm_model if llm_configured() else None,
        "embedding_backend": embed.backend_name() if n_docs else get_settings().embedding_backend,
        "plan_documents": n_docs,
        "boundaries_loaded": len(boundaries),
        "boundary_source": (prov or {}).get("source"),
        "chaos": registry.chaos_sources(),
        "login_configured": bool(get_settings().officer_passcode or get_settings().admin_passcode),
    }


@router.get("/sources")
def sources():
    return {"sources": registry.all_statuses(), "chaos": registry.chaos_sources(),
            "attribution": ["Weather data: Open-Meteo (CC BY 4.0)", "© OpenStreetMap contributors",
                            "Routing: openrouteservice", "Official alerts: NDMA SACHET",
                            "Flood data: GloFAS v4 via Open-Meteo"]}


class ChaosBody(BaseModel):
    source: str = Field(..., max_length=64)
    enabled: bool


@router.post("/admin/chaos", dependencies=[Depends(limit())])
def chaos(body: ChaosBody, user: User = Depends(admin_user)):
    try:
        registry.set_chaos(body.source, body.enabled)
    except KeyError as exc:
        raise APIError(400, "unknown_source", f"Unknown or non-switchable source '{body.source}'.") from exc
    with session_scope() as s:
        s.add(Approval(target_type="chaos", target_id=body.source, decision="enable" if body.enabled else "disable",
                       before=None, after=None, user=user.name, role=user.role,
                       note="Simulated outage (admin chaos switch)"))
    notify.publish("system", {"type": "chaos", "source": body.source, "enabled": body.enabled, "by": user.name})
    return {"chaos": registry.chaos_sources()}


class LoginBody(BaseModel):
    name: str = Field(..., max_length=64)
    passcode: str = Field(..., max_length=128)


@router.post("/auth/login", dependencies=[Depends(limit(capacity=10, refill_per_s=0.1))])
def do_login(body: LoginBody):
    user = login(body.name, body.passcode)
    return {"token": issue_token(user.name, user.role), "user": {"name": user.name, "role": user.role}}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"user": {"name": user.name, "role": user.role}}


@router.get("/notifications/stream")
async def notifications(request: Request):
    """SSE: level changes (PREPARE/ACT), sources disagree, scans, publications (§7.4)."""
    q = notify.subscribe("system")

    async def gen():
        try:
            yield f"event: hello\ndata: {json.dumps({'recent': notify.recent_system()[-10:]})}\n\n"
            last = time.monotonic()
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=5)
                    yield f"event: notice\ndata: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    if time.monotonic() - last > 15:
                        last = time.monotonic()
                        yield ": ping\n\n"
        finally:
            notify.unsubscribe("system", q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
