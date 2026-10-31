"""Features 3 and 6 API: action readiness and the live action tracker."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app import readiness, tracker
from app.auth import User, current_user
from app.db.models import AgentRun
from app.db.session import session_scope
from app.errors import APIError
from app.ratelimit import limit

router = APIRouter(prefix="/api", tags=["execution"])


def _run_exists(run_id: str) -> None:
    with session_scope() as s:
        if s.get(AgentRun, run_id) is None:
            raise APIError(404, "not_found", "Run not found")


@router.get("/runs/{run_id}/readiness")
def run_readiness(run_id: str):
    _run_exists(run_id)
    return {"by_action": readiness.readiness_for_run(run_id)}


@router.post("/runs/{run_id}/readiness", dependencies=[Depends(limit(capacity=10, refill_per_s=0.2))])
def recheck_readiness(run_id: str, user: User = Depends(current_user)):
    _run_exists(run_id)
    return {"by_action": readiness.assess_run(run_id)}


class ReadinessConfirm(BaseModel):
    status: Literal["ready", "unavailable", "unverified"]
    note: str | None = Field(None, max_length=500)


@router.patch("/readiness/{check_id}", dependencies=[Depends(limit(capacity=60, refill_per_s=1))])
def confirm_readiness(check_id: int, body: ReadinessConfirm, user: User = Depends(current_user)):
    c = readiness.confirm(check_id, body.status, body.note, user.name)
    if c is None:
        raise APIError(404, "not_found", "Check not found")
    return c


@router.get("/tasks")
def tasks(district_id: str | None = Query(None, max_length=64), status: str | None = Query(None, max_length=16),
          department: str | None = Query(None, max_length=256), run_id: str | None = Query(None, max_length=32)):
    return tracker.list_tasks(district_id=district_id, status=status, department=department, run_id=run_id)


class TaskUpdate(BaseModel):
    status: Literal["pending", "in_progress", "completed", "blocked"] | None = None
    department: str | None = Field(None, max_length=256)
    blocker: str | None = Field(None, max_length=500)
    note: str | None = Field(None, max_length=500)


@router.patch("/tasks/{task_id}", dependencies=[Depends(limit(capacity=60, refill_per_s=1))])
def update_task(task_id: str, body: TaskUpdate, user: User = Depends(current_user)):
    try:
        t = tracker.update_task(task_id, status=body.status, department=body.department, blocker=body.blocker,
                                note=body.note, user_name=user.name, role=user.role)
    except tracker.TaskError as exc:
        raise APIError(400, "invalid_update", str(exc)) from exc
    if t is None:
        raise APIError(404, "not_found", "Task not found")
    return t
