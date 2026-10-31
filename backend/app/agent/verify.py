"""VERIFY hard checks — deterministic code, never the LLM (spec §6.7).

1. Schema: the draft parses into the plan schema.
2. Grounding: every ``district_plan`` action has >= 1 citation; each cited chunk was
   actually returned by ``retrieve_plan`` in this run (generic citations by
   ``get_generic_guidance``); each quote is an exact substring of that chunk's text
   (whitespace-insensitive) and <= 300 characters.
3. Numbers: every number in the plan text exists in the evidence the code produced
   (assessment numbers, official alert text, retrieved plan text, facility names),
   with +/-0.5 tolerance for rounded percentages.
4. Facilities: every location reference is a known OSM feature / geocoded place /
   name present in the retrieved plan text; facility-like names in action text must
   also appear in the plan chunks or the OSM results.
5. Phasing: no action is scheduled in a phase that has already passed relative to t*
   (passed phases must collapse into "Do now").

Failures come back as a specific list, e.g. ``A4: quote not found on p. 117``, which
the PLAN node receives verbatim on its retry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from rapidfuzz import fuzz

from app.agent.schemas import PHASES, allowed_phases
from app.rag.text import norm_ws, numbers_in

FACILITY_WORDS = (
    "school", "hospital", "college", "shelter", "hall", "mandapam", "centre", "center", "camp", "phc",
    "dispensary", "stadium", "auditorium", "temple", "church", "mosque", "polytechnic", "university",
)
_FACILITY_NAME = re.compile(
    r"((?:[A-Z][\w.&'-]*\s){1,6}(?:" + "|".join(w.capitalize() for w in FACILITY_WORDS) + r")s?)\b"
)
GENERIC_FACILITY_PHRASES = {"relief camp", "relief camps", "community hall", "community halls", "primary school",
                            "government school", "government hospital", "cyclone shelter", "cyclone shelters",
                            "flood shelter", "flood shelters", "relief centre", "relief centres", "all hospital",
                            "control room", "emergency operations centre", "emergency operation centre"}
PHASE_NUMBERS = {48.0, 24.0, 6.0}


@dataclass
class Problem:
    action_id: str | None
    check: str
    message: str

    def __str__(self) -> str:
        return f"{self.action_id + ': ' if self.action_id else ''}{self.message}"


@dataclass
class VerifyContext:
    chunks: dict[str, dict]  # chunk_id -> chunk returned by retrieve_plan in this run
    generic_chunks: dict[str, dict]  # chunk_id -> chunk returned by get_generic_guidance
    allowed_numbers: set[float]
    osm_ids: set[str]
    osm_names: list[str]
    geocode_ids: set[str]
    geocode_names: list[str]
    t_minus_h: float | None


@dataclass
class Report:
    passed: bool
    problems: list[Problem] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "problems": [{"action_id": p.action_id, "check": p.check, "message": str(p)} for p in self.problems],
            "stats": self.stats,
        }

    def feedback(self) -> str:
        return "\n".join(f"- {p}" for p in self.problems)


def quote_in_chunk(quote: str, chunk_text: str) -> bool:
    q = norm_ws(quote)
    return bool(q) and q in norm_ws(chunk_text)


def number_allowed(x: float, allowed: set[float], tol: float = 0.5) -> bool:
    if x in allowed:
        return True
    return any(abs(x - a) <= tol for a in allowed)


def evidence_numbers(*texts: str, extra: list[float] | None = None) -> set[float]:
    out: set[float] = set(PHASE_NUMBERS)
    for t in texts:
        out.update(numbers_in(t or ""))
    for x in extra or []:
        if x is None:
            continue
        out.add(float(x))
        out.add(round(float(x), 1))
        out.add(float(round(float(x))))
    return out


def _facility_candidates(text: str) -> list[str]:
    cands = []
    for m in _FACILITY_NAME.finditer(text):
        name = m.group(1).strip()
        if name.lower() in GENERIC_FACILITY_PHRASES or len(name.split()) < 2:
            continue
        first = name.split()[0].lower()
        if first in ("the", "all", "nearest", "designated", "identified", "open", "activate", "alert", "keep", "prepare"):
            name = " ".join(name.split()[1:])
            if len(name.split()) < 2:
                continue
        cands.append(name)
    return cands


def _name_known(name: str, corpus_texts: list[str], names: list[str]) -> bool:
    """Is a place/facility name present in the plan text or the OSM/geocoder names?

    Whitespace-insensitive (PDF extraction breaks lines mid-name: "Thiru – vi – ka\n-nagar"),
    and tolerant of spacing around hyphens/dashes.
    """
    def squash(x: str) -> str:
        return re.sub(r"\s*([-–—])\s*", r"\1", norm_ws(x).lower())

    n = squash(name)
    if not n:
        return False
    for t in corpus_texts:
        if n in squash(t):
            return True
    return any(fuzz.token_set_ratio(n, squash(x or "")) >= 90 for x in names if x)


def verify_plan(draft: dict, ctx: VerifyContext, *, schema_model=None) -> Report:
    problems: list[Problem] = []
    actions = draft.get("actions") if isinstance(draft, dict) else None

    # 1. schema
    if not isinstance(actions, list):
        return Report(False, [Problem(None, "schema", "Output does not contain an 'actions' list.")])
    if schema_model is not None:
        try:
            schema_model.model_validate(draft)
        except ValidationError as exc:
            for err in exc.errors()[:10]:
                loc = ".".join(str(x) for x in err["loc"])
                problems.append(Problem(None, "schema", f"Schema error at {loc}: {err['msg']}"))
    ids = [a.get("id") for a in actions]
    if len(set(ids)) != len(ids):
        problems.append(Problem(None, "schema", "Action ids must be unique."))

    allowed = set(allowed_phases(ctx.t_minus_h))
    chunk_texts = [c["text"] for c in ctx.chunks.values()] + [c["text"] for c in ctx.generic_chunks.values()]
    n_cites = n_plan = 0

    for a in actions:
        aid = a.get("id") or "?"
        src = a.get("source")
        cites = a.get("citations") or []
        # 2. grounding
        if src == "district_plan":
            n_plan += 1
            if not cites:
                problems.append(Problem(aid, "citation", "district_plan action has no citation."))
        if src == "generic_guidance" and not cites:
            problems.append(Problem(aid, "citation", "generic_guidance action has no NDMA citation."))
        for c in cites:
            n_cites += 1
            cid = c.get("chunk_id", "")
            quote = c.get("quote", "")
            pool = ctx.chunks if src == "district_plan" else (ctx.generic_chunks if src == "generic_guidance" else {**ctx.chunks, **ctx.generic_chunks})
            chunk = pool.get(cid)
            if chunk is None:
                where = "retrieve_plan" if src == "district_plan" else "get_generic_guidance"
                problems.append(Problem(aid, "citation", f"cites chunk {cid!r} that {where} did not return in this run."))
                continue
            if len(quote) > 300:
                problems.append(Problem(aid, "citation", f"quote on p. {chunk['page']} is longer than 300 characters."))
            if not quote_in_chunk(quote, chunk["text"]):
                problems.append(Problem(aid, "citation", f"quote not found on p. {chunk['page']} ({cid}); copy a verbatim span."))
        if src == "suggested" and cites:
            pass  # allowed: supporting context, still verified above

        # 3. numbers
        text = " ".join(str(a.get(k) or "") for k in ("action", "rationale", "responsible"))
        for x in numbers_in(text):
            if not number_allowed(x, ctx.allowed_numbers):
                problems.append(Problem(aid, "number", f"number {x:g} does not appear in the assessment or the retrieved evidence."))

        # 4. facilities
        for ref in a.get("location_refs") or []:
            r = str(ref)
            if r.startswith("osm:"):
                if r not in ctx.osm_ids:
                    problems.append(Problem(aid, "facility", f"location {r} is not in the OSM results for this district."))
            elif r.startswith("area:") or r.startswith("geo:"):
                if r not in ctx.geocode_ids:
                    problems.append(Problem(aid, "facility", f"location {r} was not geocoded in this run."))
            elif not _name_known(r, chunk_texts, ctx.osm_names + ctx.geocode_names):
                problems.append(Problem(aid, "facility", f"location '{r}' is not in the plan text or OSM results."))
        for name in _facility_candidates(str(a.get("action") or "")):
            if not _name_known(name, chunk_texts, ctx.osm_names + ctx.geocode_names):
                problems.append(Problem(aid, "facility", f"facility '{name}' is not in the plan text or OSM results."))

        # 5. phasing
        ph = a.get("phase")
        if ph not in PHASES:
            problems.append(Problem(aid, "phase", f"unknown phase {ph!r}."))
        elif ph not in allowed:
            problems.append(Problem(aid, "phase", f"is phased {ph} but impact is in ~{ctx.t_minus_h:g} h; that phase has passed — use 'Do now'."))

    # numbers in the summary / questions / warnings too
    for field_name in ("evidence_summary",):
        for x in numbers_in(str(draft.get(field_name) or "")):
            if not number_allowed(x, ctx.allowed_numbers):
                problems.append(Problem(None, "number", f"{field_name}: number {x:g} does not appear in the evidence."))
    for lst in ("open_questions", "warnings"):
        for item in draft.get(lst) or []:
            for x in numbers_in(str(item)):
                if not number_allowed(x, ctx.allowed_numbers):
                    problems.append(Problem(None, "number", f"{lst}: number {x:g} does not appear in the evidence."))

    stats = {"actions": len(actions), "district_plan_actions": n_plan, "citations": n_cites,
             "allowed_phases": sorted(allowed, key=PHASES.index)}
    return Report(passed=not problems, problems=problems, stats=stats)
