"""Hazard plug-in protocol (spec §6.11).

A new hazard is one plug-in: it fetches its live data, turns it into an assessment
with deterministic, tested code, and tells the agent which plan sections matter at
each level. The trigger engine, agent, UI and communications are reused unchanged.
"""
from __future__ import annotations

from typing import Any, Protocol


class HazardPlugin(Protocol):
    name: str  # "rain", "river", "heat"

    async def fetch(self, points: list[Any]) -> Any: ...

    def assess(self, raw: Any, cfg: dict) -> Any: ...

    def plan_query_hints(self, level: str | None) -> list[str]: ...


PLAN_HINTS: dict[str, dict[str | None, list[str]]] = {
    "rain": {
        None: ["flood preparedness checklist", "monsoon preparedness responsibilities"],
        "WATCH": ["flood preparedness checklist", "early warning dissemination", "control room activation"],
        "PREPARE": ["relief camps flood shelters", "vulnerable low-lying areas inundation", "evacuation plan",
                    "pre-positioning boats pumps", "responsibility matrix departments"],
        "ACT": ["evacuation of low-lying areas", "relief camps flood shelters", "search and rescue teams",
                "hospital emergency preparedness", "power supply shutdown flooded", "dewatering pumps"],
    },
    "river": {
        None: ["river flood preparedness"],
        "WATCH": ["river flood warning dam release"],
        "PREPARE": ["evacuation river banks", "relief camps", "dam discharge warning"],
        "ACT": ["evacuation river banks villages", "relief camps", "rescue boats"],
    },
    "heat": {
        None: ["heat action plan"],
        "WATCH": ["heat wave early warning", "public awareness heat"],
        "PREPARE": ["cooling centres drinking water kiosks", "outdoor work hours", "hospital heat stroke"],
        "ACT": ["heat stroke treatment hospitals", "shift outdoor work hours", "cooling centres"],
    },
}


def plan_query_hints(hazard: str, level: str | None) -> list[str]:
    return PLAN_HINTS.get(hazard, {}).get(level, PLAN_HINTS.get(hazard, {}).get(None, []))
