"""Trigger lifecycle: deduplication, hysteresis and stand-down (spec §6.6).

State machine::

    Quiet --> WATCH | PREPARE | ACT           rule met (or official floor)
    WATCH --> PREPARE --> ACT                 rule met or escalation (upgrades are immediate)
    WATCH --> Quiet                           below (entry - margin) for 2 NEW model runs
    PREPARE/ACT --> StandDownRecommended      below (entry - margin) for 2 NEW model runs
    ACT --> PREPARE (downgrade)               same hysteresis rule
    StandDownRecommended --> PREPARE/ACT      risk rises again
    StandDownRecommended --> Closed           officer confirms (never automatic)

Rules:
* One trigger per (district, hazard, event window); level changes update it
  (CAP msgType ``Update``) — they never create a duplicate.
* Hysteresis counts NEW model runs, not scans: re-scanning the same 6-hourly run
  must not count twice, so each run id is counted at most once.
* Stand-down is only a recommendation; closing (CAP ``Cancel``) needs an officer.

Everything here is a pure function over small dataclasses so it can be unit-tested
without a database; ``app.scanner`` persists the decisions.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from app.triggers.levels import rank


@dataclass
class TriggerSnapshot:
    id: str | None
    level: str
    status: str  # active | stand_down_recommended | closed
    window_start: dt.datetime | None = None
    window_end: dt.datetime | None = None
    below_runs: list[str] = field(default_factory=list)


@dataclass
class Observation:
    level: str | None  # resolved level this scan (None = Quiet)
    hysteresis_level: str | None  # level still supported with thresholds lowered by the margin
    model_run_id: str | None
    window_start: dt.datetime | None = None
    window_end: dt.datetime | None = None
    can_open: bool = True  # False when data is stale/insufficient (§8.1: no new triggers)


@dataclass
class Decision:
    action: str  # none | open | upgrade | downgrade | count_below | reset_below | stand_down | auto_close | reactivate | hold
    level: str | None = None
    status: str | None = None
    cap_msg_type: str | None = None  # Alert | Update | None
    below_runs: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def changes_trigger(self) -> bool:
        return self.action not in ("none", "hold")


def same_event(
    a_start: dt.datetime | None,
    a_end: dt.datetime | None,
    b_start: dt.datetime | None,
    b_end: dt.datetime | None,
    slack_h: float = 24.0,
) -> bool:
    """Do two event windows overlap (with slack)? Unknown windows count as the same event."""
    if None in (a_start, a_end, b_start, b_end):
        return True
    slack = dt.timedelta(hours=slack_h)
    return a_start - slack <= b_end and b_start - slack <= a_end  # type: ignore[operator]


def step(existing: TriggerSnapshot | None, obs: Observation, runs_required: int = 2) -> Decision:
    """Decide what one scan does to the (district, hazard, event) trigger."""
    # --- No live trigger: maybe open one ---------------------------------
    if existing is None or existing.status == "closed":
        if obs.level is None:
            return Decision("none", reason="Quiet")
        if not obs.can_open:
            return Decision("hold", reason="Data too old or insufficient: no new triggers (§8.1).")
        return Decision("open", level=obs.level, status="active", cap_msg_type="Alert", reason=f"{obs.level} rule met")

    # --- Stand-down recommended: only reactivates if risk rises again ----
    if existing.status == "stand_down_recommended":
        if rank(obs.level) >= rank("PREPARE") and obs.can_open:
            return Decision(
                "reactivate",
                level=obs.level,
                status="active",
                cap_msg_type="Update",
                reason=f"Risk rose again ({obs.level}); stand-down recommendation withdrawn.",
            )
        return Decision("none", reason="Awaiting officer decision on stand-down.")

    # --- Active trigger ---------------------------------------------------
    if rank(obs.level) > rank(existing.level):
        if not obs.can_open:
            return Decision("hold", reason="Data too old to escalate.")
        return Decision(
            "upgrade", level=obs.level, status="active", cap_msg_type="Update",
            reason=f"Escalated {existing.level} -> {obs.level}",
        )

    if rank(obs.hysteresis_level) < rank(existing.level):
        if obs.model_run_id is None:
            return Decision("hold", reason="Below exit threshold but model run unknown; not counted.")
        if obs.model_run_id in existing.below_runs:
            return Decision("none", below_runs=list(existing.below_runs),
                            reason="Same model run already counted (hysteresis counts new runs only).")
        below = [*existing.below_runs, obs.model_run_id]
        if len(below) < runs_required:
            return Decision("count_below", below_runs=below,
                            reason=f"Below (entry - margin): {len(below)} of {runs_required} new model runs.")
        target = obs.hysteresis_level
        if target is None:
            if existing.level == "WATCH":
                return Decision("auto_close", level="WATCH", status="closed", cap_msg_type=None,
                                reason="WATCH ended: below (entry - margin) for consecutive new model runs.")
            return Decision("stand_down", level=existing.level, status="stand_down_recommended",
                            cap_msg_type=None, below_runs=below,
                            reason="Stand-down recommended: below (entry - margin) for consecutive new model runs. "
                                   "An officer must confirm closure.")
        return Decision("downgrade", level=target, status="active", cap_msg_type="Update",
                        reason=f"Downgraded {existing.level} -> {target} after consecutive new model runs below exit.")

    if existing.below_runs:
        return Decision("reset_below", below_runs=[], reason="Back above exit threshold; hysteresis counter reset.")
    return Decision("none", reason="Level unchanged")
