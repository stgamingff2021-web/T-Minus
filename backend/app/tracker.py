"""Feature 6 — live action tracker.

An approved action becomes a task: department (from the action's "responsible"), status
(pending → in progress → completed, or blocked with a reason) and a due time derived from its
phase and the expected impact time of the run that proposed it (``config/hazards.yaml`` →
``tracker``). Overdue = past due and not completed. Rejecting an action cancels its task.
Every change is written to the append-only audit log.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from sqlalchemy import select

from app.config import get_config
from app.db.models import ActionItemRow, ActionTask, AgentRun, Approval, utcnow
from app.db.session import session_scope

STATUSES = ("pending", "in_progress", "completed", "blocked")


def _t_star(run: AgentRun) -> dt.datetime | None:
    iso = (((run.state or {}).get("assessment") or {}).get("headline") or {}).get("expected_impact_utc")
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def due_at(phase: str, t_star: dt.datetime | None, approved_at: dt.datetime) -> dt.datetime | None:
    cfg = get_config().hazards.get("tracker", {})
    if phase == "Do now":
        return approved_at + dt.timedelta(hours=float(cfg.get("do_now_due_h", 2)))
    offset = (cfg.get("phase_offsets_h") or {}).get(phase)
    if offset is None or t_star is None:
        return None
    return t_star - dt.timedelta(hours=float(offset))


def on_decision(s, action: ActionItemRow, decision: str, user_name: str) -> None:
    """Called inside the action-decision transaction."""
    task = s.execute(select(ActionTask).where(ActionTask.action_id == action.id)).scalar_one_or_none()
    if decision == "approve":
        run = s.get(AgentRun, action.run_id)
        now = utcnow()
        if task is None:
            s.add(ActionTask(action_id=action.id, run_id=action.run_id, district_id=run.district_id,
                             department=action.responsible, status="pending",
                             due_at=due_at(action.phase, _t_star(run), now), updated_by=user_name))
        elif task.status == "cancelled":
            task.status, task.updated_at, task.updated_by = "pending", now, user_name
    elif decision in ("reject", "reset") and task is not None and task.status != "completed":
        task.status, task.updated_at, task.updated_by = "cancelled", utcnow(), user_name


def _overdue(t: ActionTask, now: dt.datetime) -> bool:
    return bool(t.due_at and t.due_at < now and t.status not in ("completed", "cancelled"))


def task_dict(t: ActionTask, a: ActionItemRow | None, district_name: str | None, now: dt.datetime) -> dict:
    return {
        "id": t.id, "action_id": t.action_id, "run_id": t.run_id, "district_id": t.district_id,
        "district_name": district_name, "action": (a.edited_text or a.action) if a else None,
        "phase": a.phase if a else None, "source": a.source if a else None, "key": a.key if a else None,
        "department": t.department, "status": t.status, "blocker": t.blocker, "note": t.note,
        "due_at": t.due_at.isoformat() if t.due_at else None, "overdue": _overdue(t, now),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None, "updated_by": t.updated_by,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


def list_tasks(*, district_id: str | None = None, status: str | None = None, department: str | None = None,
               run_id: str | None = None) -> dict:
    from app.readiness import readiness_for_actions

    cfg = get_config()
    now = utcnow()
    with session_scope() as s:
        q = select(ActionTask, ActionItemRow).join(ActionItemRow, ActionItemRow.id == ActionTask.action_id, isouter=True)\
            .where(ActionTask.status != "cancelled")
        if district_id:
            q = q.where(ActionTask.district_id == district_id)
        if run_id:
            q = q.where(ActionTask.run_id == run_id)
        rows = s.execute(q).all()
        all_tasks = [task_dict(t, a, (cfg.district(t.district_id).name if cfg.district(t.district_id) else t.district_id), now)
                     for t, a in rows]
    readiness = readiness_for_actions([t["action_id"] for t in all_tasks])
    for t in all_tasks:
        r = readiness.get(t["action_id"]) or {}
        t["readiness"] = r.get("overall")
        t["readiness_unavailable"] = [c["label"] for c in r.get("checks", []) if c["status"] == "unavailable"]
    counts = Counter(t["status"] for t in all_tasks)
    summary = {k: counts.get(k, 0) for k in STATUSES}
    summary["overdue"] = sum(t["overdue"] for t in all_tasks)
    summary["problems"] = sum(1 for t in all_tasks if t["status"] == "blocked" or t["readiness_unavailable"])
    departments = sorted({t["department"] for t in all_tasks if t["department"]})
    shown = [t for t in all_tasks if (not status or t["status"] == status or (status == "overdue" and t["overdue"]))
             and (not department or t["department"] == department)]
    order = {"blocked": 0, "pending": 1, "in_progress": 2, "completed": 3}
    shown.sort(key=lambda t: (not t["overdue"], order.get(t["status"], 9), t["due_at"] or "9999"))
    return {"tasks": shown, "counts": summary, "departments": departments}


class TaskError(ValueError):
    pass


def update_task(task_id: str, *, status: str | None, department: str | None, blocker: str | None,
                note: str | None, user_name: str, role: str) -> dict | None:
    now = utcnow()
    with session_scope() as s:
        t = s.get(ActionTask, task_id)
        if t is None:
            return None
        if t.status == "cancelled":
            raise TaskError("This action was rejected; its task is closed.")
        before = f"{t.status} · {t.department or '-'}"
        if status:
            if status not in STATUSES:
                raise TaskError(f"status must be one of {', '.join(STATUSES)}")
            if status == "blocked" and not (blocker or t.blocker):
                raise TaskError("Say what is blocking it.")
            t.status = status
            t.completed_at = now if status == "completed" else None
            if status != "blocked":
                t.blocker = None
        if blocker is not None and (status or t.status) == "blocked":
            t.blocker = blocker.strip() or t.blocker
        if department is not None:
            t.department = department.strip() or None
        if note is not None:
            t.note = note.strip() or None
        t.updated_at, t.updated_by = now, user_name
        s.add(Approval(target_type="task", target_id=t.id, decision=f"task_{t.status}", before=before,
                       after=f"{t.status} · {t.department or '-'}" + (f" · blocked: {t.blocker}" if t.blocker else ""),
                       user=user_name, role=role, note=note))
        a = s.get(ActionItemRow, t.action_id)
        d = get_config().district(t.district_id)
        return task_dict(t, a, d.name if d else t.district_id, now)
