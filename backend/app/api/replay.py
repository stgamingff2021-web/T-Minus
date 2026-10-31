"""Replay endpoints (spec §6.10)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.common import iso
from app.config import get_config
from app.db.models import ReplayRun
from app.db.session import session_scope
from app.errors import APIError
from app.ratelimit import limit
from app.replay import runner

router = APIRouter(prefix="/api", tags=["replay"])


@router.get("/replay/events")
def events():
    cfg = get_config()
    with session_scope() as s:
        latest = {}
        for r in s.execute(select(ReplayRun).order_by(ReplayRun.started_at.desc()).limit(50)).scalars():
            latest.setdefault(r.event_key, {"id": r.id, "status": r.status, "started_at": iso(r.started_at),
                                            "finished_at": iso(r.finished_at), "error": r.error})
    return {"events": [{**{k: e.get(k) for k in ("key", "name", "district", "as_of_start", "as_of_end", "truth_start",
                                                  "truth_end", "quiet_start", "quiet_end")},
                        "latest": latest.get(e["key"])} for e in cfg.replay.get("events", [])],
            "models": cfg.replay.get("models", [])}


class ReplayBody(BaseModel):
    event_key: str = Field(..., max_length=64)


@router.post("/replay", status_code=202, dependencies=[Depends(limit(capacity=4, refill_per_s=1 / 60))])
async def start_replay(body: ReplayBody):
    if not any(e["key"] == body.event_key for e in get_config().replay.get("events", [])):
        raise APIError(404, "unknown_event", f"Unknown replay event '{body.event_key}'.")
    return {"replay_id": runner.start(body.event_key)}


@router.get("/replay/{replay_id}")
def get_replay(replay_id: str):
    with session_scope() as s:
        r = s.get(ReplayRun, replay_id)
        if r is None:
            raise APIError(404, "not_found", "Replay not found")
        return {"id": r.id, "event_key": r.event_key, "status": r.status, "started_at": iso(r.started_at),
                "finished_at": iso(r.finished_at), "error": r.error, "result": r.result, "fetch_ids": r.fetch_ids}
