"""Agent output schemas (spec §6.8).

Two layers:
* ``PLAN_DRAFT_SCHEMA`` — what the LLM is allowed to produce (JSON Schema for
  structured outputs). It contains only chunk ids + verbatim quotes for citations;
  document titles and page numbers are filled in by code from the chunk store, so the
  model cannot invent a page number. ``key_numbers`` are never produced by the model.
* Pydantic models (``Citation``, ``ActionItem``, ``ActionPlan``) — the validated,
  enriched plan stored and shown in the UI.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

Phase = Literal["Do now", "T-48h", "T-24h", "T-6h"]
PHASES: tuple[str, ...] = ("Do now", "T-48h", "T-24h", "T-6h")
PHASE_HOURS = {"T-48h": 48, "T-24h": 24, "T-6h": 6}
Source = Literal["district_plan", "generic_guidance", "suggested"]


class Citation(BaseModel):
    document_id: str
    document_title: str
    page: int
    chunk_id: str
    quote: str = Field(max_length=300)
    section: str | None = None
    source_url: str | None = None
    kind: str | None = None


class ActionItem(BaseModel):
    id: str
    phase: Phase
    action: str
    responsible: str | None = None
    location_refs: list[str] = []
    source: Source
    citations: list[Citation] = []
    rationale: str
    status: Literal["proposed", "approved", "edited", "rejected"] = "proposed"
    problems: list[str] = []


class ActionPlan(BaseModel):
    trigger_id: str | None
    district_id: str
    hazard: Literal["rain", "river", "heat"]
    level: Literal["WATCH", "PREPARE", "ACT", "QUIET"]
    generated_at: dt.datetime
    evidence_summary: str
    key_numbers: dict[str, float]
    actions: list[ActionItem]
    open_questions: list[str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# JSON Schemas for structured outputs (additionalProperties: false everywhere,
# no numeric/string-length constraints — those are validated in code).
# ---------------------------------------------------------------------------

_DRAFT_CITATION = {
    "type": "object",
    "properties": {
        "chunk_id": {"type": "string", "description": "chunk_id exactly as returned by retrieve_plan / get_generic_guidance"},
        "quote": {"type": "string", "description": "A contiguous, verbatim span copied from that chunk's text (<= 300 characters). No ellipses, no paraphrase."},
    },
    "required": ["chunk_id", "quote"],
    "additionalProperties": False,
}

PLAN_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_summary": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "A1, A2, ..."},
                    "phase": {"type": "string", "enum": list(PHASES)},
                    "action": {"type": "string"},
                    "responsible": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "location_refs": {"type": "array", "items": {"type": "string"}},
                    "source": {"type": "string", "enum": ["district_plan", "generic_guidance", "suggested"]},
                    "citations": {"type": "array", "items": _DRAFT_CITATION},
                    "rationale": {"type": "string"},
                },
                "required": ["id", "phase", "action", "responsible", "location_refs", "source", "citations", "rationale"],
                "additionalProperties": False,
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["evidence_summary", "actions", "open_questions", "warnings"],
    "additionalProperties": False,
}

CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "revise"]},
        "issues": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "issues", "gaps"],
    "additionalProperties": False,
}

COMMS_SCHEMA = {
    "type": "object",
    "properties": {
        "sms_en": {"type": "string"},
        "sms_ta": {"type": "string"},
        "headline_en": {"type": "string"},
        "description_en": {"type": "string"},
        "instruction_en": {"type": "string"},
        "headline_ta": {"type": "string"},
        "description_ta": {"type": "string"},
        "instruction_ta": {"type": "string"},
    },
    "required": ["sms_en", "sms_ta", "headline_en", "description_en", "instruction_en",
                 "headline_ta", "description_ta", "instruction_ta"],
    "additionalProperties": False,
}


def allowed_phases(t_minus_h: float | None) -> list[str]:
    """Phases still in the future; passed phases collapse into "Do now" (§6.7 PLAN)."""
    if t_minus_h is None:
        return list(PHASES)
    return ["Do now"] + [p for p in ("T-48h", "T-24h", "T-6h") if t_minus_h > PHASE_HOURS[p]]
