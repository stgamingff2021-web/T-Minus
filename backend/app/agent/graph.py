"""The T-Minus agent as an explicit LangGraph state graph (spec §6.7).

    START -> ASSESS -> GATHER -> PLAN -> VERIFY --pass--> COMMUNICATE -> END
                          |                 |--fail (max 2 retries, specific feedback)--> PLAN
                          |                 '--retries exhausted--> PARTIAL -> END
                          '--no LLM available--> END ("temporarily unavailable"; retrieval-only evidence kept)

* ASSESS      code: reads the scanner's assessment + official alerts; deterministic summary.
* GATHER      LLM tool loop over the registry (budget: 20 tool calls per run, soft timeout).
* PLAN        LLM structured output (chunk ids + verbatim quotes only).
* VERIFY      code hard checks (verify.py), then an LLM critic pass for coherence/gaps.
* COMMUNICATE LLM drafts EN/TA messages + CAP text; code validates lengths, wording, numbers,
              builds the CAP XML (validated against the OASIS XSD) and the briefing.

Every step and tool call is emitted to the live trace. Nothing is sent anywhere: the
run ends in "Wait for human approval".
"""
from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.agent import prompts
from app.agent.llm import FailoverLLM, LLMRefused, LLMUnavailable
from app.agent.schemas import (
    COMMS_SCHEMA,
    CRITIC_SCHEMA,
    PLAN_DRAFT_SCHEMA,
    allowed_phases,
)
from app.agent.tools import GATHER_TOOLS, BudgetExceeded, ToolContext, call_tool, claude_tools, wrap
from app.agent.verify import VerifyContext, evidence_numbers, verify_plan
from app.comms import sms
from app.comms.messages import TAMIL_WORDING, message_mentions_probability, pct_int, probability_wording, wording_with_number
from app.config import get_config
from app.hazards.base import plan_query_hints
from app.rag import retrieve


# ---------------------------------------------------------------------------
# State + context
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    run_id: str
    district_id: str
    district_name: str
    hazard: str
    trigger_id: str | None
    level: str
    assessment: dict | None
    official_alerts: dict | None
    evidence_summary_code: str
    gather_note: str
    draft_plan: dict | None
    verification_report: dict | None
    critic: dict | None
    plan_feedback: str | None
    attempt: int
    communications: dict | None
    warnings: list[str]
    errors: list[str]
    status: str  # running | completed | partial | unavailable | failed


@dataclass
class RunCtx:
    run_id: str
    district_id: str
    district_name: str
    hazard: str
    tools: ToolContext
    llm: FailoverLLM | None
    emit: Callable[[str, dict], None]
    t0: float = field(default_factory=time.perf_counter)
    soft_timeout_s: float = 120.0
    max_attempts: int = 3
    tokens_in: int = 0
    tokens_out: int = 0
    plan_messages: list[dict] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def time_left(self) -> float:
        return self.soft_timeout_s - self.elapsed()

    def account(self, res) -> None:
        self.tokens_in += res.input_tokens
        self.tokens_out += res.output_tokens
        if res.model not in self.models_used:
            self.models_used.append(res.model)
        self.emit("llm", {"model": res.model, "ms": res.duration_ms, "in": res.input_tokens, "out": res.output_tokens,
                          "stop": res.stop_reason})
        for th in res.thinking:
            if th.strip():
                self.emit("thinking", {"text": th.strip()[:1200]})
        if self.llm is not None:
            for ev in self.llm.events:
                self.emit("retry", {"kind": "model_failover", "message": ev})
            self.llm.events.clear()


def _ctx(config) -> RunCtx:
    return config["configurable"]["ctx"]


class DraftCitation(BaseModel):
    chunk_id: str
    quote: str


class DraftAction(BaseModel):
    id: str
    phase: Literal["Do now", "T-48h", "T-24h", "T-6h"]
    action: str
    responsible: str | None
    location_refs: list[str]
    source: Literal["district_plan", "generic_guidance", "suggested"]
    citations: list[DraftCitation]
    rationale: str


class DraftPlan(BaseModel):
    evidence_summary: str
    actions: list[DraftAction]
    open_questions: list[str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def code_summary(card: dict | None, district: str) -> str:
    """Deterministic evidence summary: numbers are inserted by code, never re-rounded."""
    if not card or card.get("status") == "no_assessment":
        return f"No scanner assessment is available yet for {district}; plan generated on request without a live trigger."
    h = card.get("headline") or {}
    parts = [f"{district}: level {card.get('level')}."]
    if h.get("probability_pct") is not None:
        parts.append(
            f"{h['probability_pct']}% ({h.get('members_exceeding')} of {h.get('valid_members')} forecasts) show a 24-hour total "
            f">= {h.get('threshold_mm')} mm within {h.get('within_h')} h ({card.get('model')}, run {card.get('model_run')})."
        )
    if h.get("t_minus_h") is not None:
        parts.append(f"Expected impact in ~{h['t_minus_h']} h; earliest plausible onset in ~{h.get('earliest_onset_h')} h.")
    if card.get("official_alerts"):
        parts.append("Official alerts: " + "; ".join(f"{a.get('agency')} {a.get('color') or a.get('severity')}" for a in card["official_alerts"]) + ".")
    if card.get("escalations"):
        parts.append("Escalated for: " + ", ".join(card["escalations"]) + ".")
    if card.get("sources_disagree"):
        parts.append("Sources disagree — the more cautious level is kept.")
    if card.get("degraded"):
        parts.append("Data degraded: fewer valid ensemble members than required.")
    return " ".join(parts)


def key_numbers(card: dict | None) -> dict[str, float]:
    if not card or card.get("status") == "no_assessment":
        return {}
    h = card.get("headline") or {}
    raw = {
        "probability_pct": h.get("probability_pct"),
        "members_exceeding": h.get("members_exceeding"),
        "valid_members": h.get("valid_members"),
        "members_in_model": card.get("members_in_model"),
        "threshold_mm": h.get("threshold_mm"),
        "window_h": h.get("within_h"),
        "t_minus_h": h.get("t_minus_h"),
        "earliest_onset_h": h.get("earliest_onset_h"),
        "antecedent_rain_72h_mm": card.get("antecedent_rain_72h_mm"),
    }
    return {k: float(v) for k, v in raw.items() if v is not None}


def _walk_numbers(obj: Any, out: list[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_numbers(v, out)
    elif isinstance(obj, str):
        pass


def evidence_pack(ctx: RunCtx, state: AgentState) -> str:
    t = ctx.tools
    named_fac = [{k: f.get(k) for k in ("id", "name", "type")} for f in t.facilities.values() if f.get("name")][:80]
    pack = {
        "district": state.get("district_name"),
        "hazard": state.get("hazard"),
        "level": state.get("level"),
        "assessment": state.get("assessment"),
        "official_alerts": (state.get("official_alerts") or {}).get("alerts"),
        "district_plan_available": retrieve.district_has_plan(ctx.district_id),
        "district_plan_chunks": [{k: c.get(k) for k in ("chunk_id", "document_title", "page", "section", "text")}
                                 for c in t.retrieved.values()],
        "generic_guidance_chunks": [{k: c.get(k) for k in ("chunk_id", "document_title", "page", "section", "text")}
                                    for c in t.generic.values()],
        "plan_queries_not_found": t.not_found_queries,
        "facilities_osm": named_fac,
        "facility_counts": _counts(t.facilities),
        "geocoded_places": [{k: g.get(k) for k in ("id", "name", "display_name")} for g in t.geocodes.values()],
        "unresolved_places": t.unresolved_places,
        "routes": [{"id": r["id"], "origin": r["origin"], "destination": r["destination"],
                    "distance_km": round((r.get("distance_m") or 0) / 1000, 2),
                    "duration_min": round((r.get("duration_s") or 0) / 60, 1), "avoided": r["avoided"]} for r in t.routes],
        "tool_errors": t.errors,
        "gather_note": state.get("gather_note"),
    }
    return json.dumps(pack, ensure_ascii=False, default=str)


def _counts(facilities: dict) -> dict:
    out: dict[str, int] = {}
    for f in facilities.values():
        out[f["type"]] = out.get(f["type"], 0) + 1
    return out


def verify_context(ctx: RunCtx, state: AgentState) -> VerifyContext:
    t = ctx.tools
    nums: list[float] = []
    _walk_numbers(state.get("assessment"), nums)
    _walk_numbers((state.get("official_alerts") or {}).get("alerts"), nums)
    texts = [c["text"] for c in t.retrieved.values()] + [c["text"] for c in t.generic.values()]
    texts += [json.dumps(state.get("assessment") or {}, default=str), json.dumps(state.get("official_alerts") or {}, default=str)]
    texts += [f.get("name") or "" for f in t.facilities.values()]
    texts += [(g.get("name") or "") + " " + (g.get("display_name") or "") for g in t.geocodes.values()]
    route_nums = []
    for r in t.routes:
        route_nums += [round((r.get("distance_m") or 0) / 1000, 2), round((r.get("duration_s") or 0) / 60, 1)]
    allowed = evidence_numbers(*texts, extra=nums + route_nums)
    head = (state.get("assessment") or {}).get("headline") or {}
    return VerifyContext(
        chunks=t.retrieved, generic_chunks=t.generic, allowed_numbers=allowed,
        osm_ids=set(t.facilities), osm_names=[f.get("name") for f in t.facilities.values() if f.get("name")],
        geocode_ids=set(t.geocodes), geocode_names=[g["name"] for g in t.geocodes.values()],
        t_minus_h=head.get("t_minus_h"),
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def node_assess(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    t0 = time.perf_counter()
    ctx.emit("step", {"node": "ASSESS", "status": "start"})
    card = await call_tool(ctx.tools, "get_assessment", {}, by="code")
    alerts = await call_tool(ctx.tools, "get_official_alerts", {}, by="code")
    level = (card.get("level") or "QUIET") if card.get("status") != "no_assessment" else "QUIET"
    summary = code_summary(card, state["district_name"])
    warnings = []
    if card.get("status") == "no_assessment":
        warnings.append("No live assessment yet for this district (scanner has not completed a successful scan).")
    if alerts.get("status") != "ok":
        warnings.append(f"Official alerts unavailable: {alerts.get('error')}")
    if card.get("degraded"):
        warnings.append("Ensemble data degraded (fewer valid members than required).")
    if card.get("data_time"):
        age_h = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(card["data_time"])).total_seconds() / 3600
        if age_h > float(get_config().hazards["rain"].get("max_data_age_h", 6)):
            warnings.append(f"STALE data: the assessment was fetched {age_h:.1f} h ago; live sources could not be refreshed.")
    if card.get("sources_disagree"):
        warnings.append("Sources disagree (official alert vs ensemble); the more cautious level is used.")
    if not retrieve.district_has_plan(ctx.district_id):
        warnings.append("No district plan available for this district — upload the official DDMP. Only labelled NDMA "
                        "generic guidance can be cited.")
    ctx.emit("step", {"node": "ASSESS", "status": "done", "ms": int((time.perf_counter() - t0) * 1000),
                      "summary": summary, "level": level})
    return {"assessment": card if card.get("status") != "no_assessment" else None, "official_alerts": alerts,
            "evidence_summary_code": summary, "level": level, "warnings": warnings, "attempt": 0, "errors": []}


async def node_gather(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    t0 = time.perf_counter()
    ctx.emit("step", {"node": "GATHER", "status": "start"})
    hints = plan_query_hints(state["hazard"], None if state["level"] == "QUIET" else state["level"])
    if ctx.llm is None:
        ctx.emit("error", {"node": "GATHER", "message": "LLM unavailable (no API key configured): running retrieval-only "
                                                         "mode — relevant plan sections are shown, no AI plan is drafted."})
        await _deterministic_gather(ctx, hints)
        ctx.emit("step", {"node": "GATHER", "status": "done", "ms": int((time.perf_counter() - t0) * 1000), "mode": "retrieval-only"})
        return {"status": "unavailable", "gather_note": "Planner temporarily unavailable (no LLM configured)."}

    user = (f"<evidence>\n{json.dumps({'district': state['district_name'], 'hazard': state['hazard'], 'level': state['level'], 'assessment': state.get('assessment'), 'official_alerts': (state.get('official_alerts') or {}).get('alerts')}, ensure_ascii=False, default=str)}\n</evidence>\n\n"
            f"Suggested topics for this level (adapt as needed): {', '.join(hints)}.\n"
            "Gather the evidence now.")
    messages: list[dict] = [{"role": "user", "content": user}]
    note = ""
    try:
        for _ in range(12):
            remaining = ctx.tools.max_calls - ctx.tools.calls
            if remaining <= 0:
                note = "Tool budget exhausted."
                ctx.emit("retry", {"kind": "budget", "message": f"Tool budget of {ctx.tools.max_calls} calls reached; moving to PLAN."})
                break
            if ctx.elapsed() > ctx.soft_timeout_s * 0.5:
                note = "Stopped gathering at half the soft timeout to leave time for planning and verification."
                ctx.emit("retry", {"kind": "timeout", "message": note})
                break
            res = await ctx.llm.chat(system=prompts.GATHER.format(remaining=remaining), messages=messages,
                                     tools=claude_tools(GATHER_TOOLS), max_tokens=8000, effort="medium")
            ctx.account(res)
            messages.append({"role": "assistant", "content": res.content})
            if res.stop_reason == "pause_turn":
                continue
            if not res.tool_uses:
                note = res.text.strip()
                break
            results = []
            for tu in res.tool_uses:
                try:
                    out = await call_tool(ctx.tools, tu["name"], tu["input"] or {})
                except BudgetExceeded as exc:
                    out = {"status": "error", "message": f"{exc}; finish gathering now."}
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": wrap(tu["name"], out),
                                "is_error": out.get("status") == "error"})
            messages.append({"role": "user", "content": results})
    except (LLMUnavailable, LLMRefused) as exc:
        ctx.emit("error", {"node": "GATHER", "message": f"LLM failed: {exc}. Falling back to retrieval-only mode."})
        await _deterministic_gather(ctx, hints)
        ctx.emit("step", {"node": "GATHER", "status": "done", "ms": int((time.perf_counter() - t0) * 1000), "mode": "retrieval-only"})
        return {"status": "unavailable", "gather_note": f"Planner temporarily unavailable: {exc}",
                "errors": [*state.get("errors", []), str(exc)]}
    if not ctx.tools.retrieved and retrieve.district_has_plan(ctx.district_id):
        await _deterministic_gather(ctx, hints[:3])  # make sure PLAN has something to ground on
    ctx.emit("step", {"node": "GATHER", "status": "done", "ms": int((time.perf_counter() - t0) * 1000),
                      "note": note[:600], "chunks": len(ctx.tools.retrieved), "facilities": len(ctx.tools.facilities),
                      "geocodes": len(ctx.tools.geocodes), "tool_calls": ctx.tools.calls})
    return {"gather_note": note}


async def _deterministic_gather(ctx: RunCtx, hints: list[str]) -> None:
    for q in hints[:4]:
        if ctx.tools.calls >= ctx.tools.max_calls:
            return
        await call_tool(ctx.tools, "retrieve_plan", {"query": q, "k": 4}, by="code")
    if not retrieve.district_has_plan(ctx.district_id) and retrieve.has_generic() and ctx.tools.calls < ctx.tools.max_calls:
        await call_tool(ctx.tools, "get_generic_guidance", {"query": hints[0] if hints else "flood preparedness"}, by="code")
    if not ctx.tools.facilities and ctx.tools.calls < ctx.tools.max_calls:
        await call_tool(ctx.tools, "find_facilities", {"types": ["hospital", "shelter", "school"]}, by="code")


async def node_plan(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    attempt = int(state.get("attempt", 0)) + 1
    t0 = time.perf_counter()
    ctx.emit("step", {"node": "PLAN", "status": "start", "attempt": attempt})
    head = (state.get("assessment") or {}).get("headline") or {}
    allowed = allowed_phases(head.get("t_minus_h"))
    if attempt == 1:
        ctx.plan_messages = [{"role": "user", "content":
                              f"<evidence>\n{evidence_pack(ctx, state)}\n</evidence>\n\n"
                              f"Draft the action plan for {state['district_name']} ({state['hazard']}, level {state['level']}). "
                              f"Allowed phases: {', '.join(allowed)}."}]
    else:
        ctx.plan_messages.append({"role": "user", "content":
                                  "The verifier rejected the previous draft. Fix every problem below and return the complete "
                                  f"corrected plan:\n{state.get('plan_feedback')}"})
    try:
        res = await ctx.llm.chat(system=prompts.PLAN.format(allowed_phases=", ".join(allowed)),
                                 messages=ctx.plan_messages, output_schema=PLAN_DRAFT_SCHEMA, max_tokens=24000)
    except (LLMUnavailable, LLMRefused) as exc:
        ctx.emit("error", {"node": "PLAN", "message": str(exc)})
        return {"attempt": attempt, "draft_plan": state.get("draft_plan"), "errors": [*state.get("errors", []), str(exc)],
                "status": "failed" if not state.get("draft_plan") else state.get("status", "running")}
    ctx.account(res)
    ctx.plan_messages.append({"role": "assistant", "content": res.content})
    draft = res.parsed if isinstance(res.parsed, dict) else {}
    ctx.emit("step", {"node": "PLAN", "status": "done", "attempt": attempt, "ms": int((time.perf_counter() - t0) * 1000),
                      "actions": len(draft.get("actions") or [])})
    return {"draft_plan": draft, "attempt": attempt}


async def node_verify(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    t0 = time.perf_counter()
    ctx.emit("step", {"node": "VERIFY", "status": "start", "attempt": state.get("attempt")})
    draft = state.get("draft_plan") or {}
    report = verify_plan(draft, verify_context(ctx, state), schema_model=DraftPlan)
    ctx.emit("verify", report.as_dict())
    critic = None
    feedback = report.feedback() if not report.passed else None
    if report.passed and ctx.llm is not None and ctx.elapsed() < ctx.soft_timeout_s:
        try:
            res = await ctx.llm.chat(
                system=prompts.CRITIC,
                messages=[{"role": "user", "content": f"<evidence>\n{evidence_pack(ctx, state)}\n</evidence>\n\n"
                                                      f"<draft_plan>\n{json.dumps(draft, ensure_ascii=False)}\n</draft_plan>"}],
                output_schema=CRITIC_SCHEMA, max_tokens=6000, effort="medium")
            ctx.account(res)
            critic = res.parsed
            ctx.emit("critic", critic)
            if critic.get("verdict") == "revise":
                feedback = "Critic review:\n" + "\n".join(f"- {i}" for i in critic.get("issues") or [])
        except (LLMUnavailable, LLMRefused) as exc:
            ctx.emit("error", {"node": "VERIFY", "message": f"Critic pass skipped: {exc}"})
    ctx.emit("step", {"node": "VERIFY", "status": "done", "ms": int((time.perf_counter() - t0) * 1000),
                      "passed": report.passed, "problems": len(report.problems),
                      "critic": (critic or {}).get("verdict")})
    return {"verification_report": report.as_dict(), "critic": critic, "plan_feedback": feedback}


def route_after_verify(state: AgentState, config) -> str:
    ctx = _ctx(config)
    rep = state.get("verification_report") or {}
    attempt = int(state.get("attempt", 1))
    critic = state.get("critic") or {}
    if state.get("status") == "failed":
        return "partial"
    if rep.get("passed"):
        if critic.get("verdict") == "revise" and attempt < ctx.max_attempts and ctx.elapsed() < ctx.soft_timeout_s:
            ctx.emit("retry", {"kind": "critic", "attempt": attempt + 1, "message": state.get("plan_feedback")})
            return "plan"
        return "communicate"
    if attempt < ctx.max_attempts and ctx.elapsed() < ctx.soft_timeout_s * 2:
        ctx.emit("retry", {"kind": "verify", "attempt": attempt + 1, "message": state.get("plan_feedback")})
        return "plan"
    return "partial"


def route_after_gather(state: AgentState, config) -> str:
    return "end" if state.get("status") == "unavailable" else "plan"


async def node_communicate(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    t0 = time.perf_counter()
    ctx.emit("step", {"node": "COMMUNICATE", "status": "start"})
    pm = get_config().hazards.get("public_message", {})
    en_max, ta_max = int(pm.get("english_max_chars", 160)), int(pm.get("tamil_max_chars", 134))
    head = (state.get("assessment") or {}).get("headline") or {}
    p = None if head.get("probability_pct") is None else float(head["probability_pct"]) / 100.0
    comms: dict[str, Any] = {"drafted": False}
    phrase = probability_wording(p) if state["hazard"] in ("rain", "heat") else None
    if not phrase:
        reason = ("probability below 30%" if p is not None else "no probability for this hazard")
        comms["reason"] = f"No public message drafted ({reason}), per §6.9."
        ctx.emit("step", {"node": "COMMUNICATE", "status": "done", "ms": int((time.perf_counter() - t0) * 1000),
                          "public_message": False, "reason": comms["reason"]})
        return {"communications": comms, "status": "completed"}
    wording = wording_with_number(p)
    plan_brief = {k: (state.get("draft_plan") or {}).get(k) for k in ("evidence_summary", "actions")}
    msgs = [{"role": "user", "content":
             f"<evidence>\n{json.dumps({'district': state['district_name'], 'level': state['level'], 'assessment': state.get('assessment'), 'plan': plan_brief}, ensure_ascii=False, default=str)}\n</evidence>\n\n"
             f"Probability wording to use verbatim in English: \"{wording}\". Tamil wording hint: \"{TAMIL_WORDING.get(phrase, '')} ({pct_int(p)}%)\"."}]
    draft: dict = {}
    problems: list[str] = []
    for attempt in (1, 2):
        try:
            res = await ctx.llm.chat(system=prompts.COMMS.format(en_max=en_max, ta_max=ta_max, wording=wording, pct=pct_int(p)),
                                     messages=msgs, output_schema=COMMS_SCHEMA, max_tokens=6000, effort="medium")
        except (LLMUnavailable, LLMRefused) as exc:
            ctx.emit("error", {"node": "COMMUNICATE", "message": str(exc)})
            break
        ctx.account(res)
        draft = res.parsed or {}
        problems = comms_problems(draft, p, wording, en_max, ta_max, verify_context(ctx, state).allowed_numbers)
        if not problems:
            break
        if attempt == 1:
            ctx.emit("retry", {"kind": "comms", "message": "; ".join(problems)})
            msgs += [{"role": "assistant", "content": res.content},
                     {"role": "user", "content": "Fix these problems and return all fields again:\n" + "\n".join(f"- {x}" for x in problems)}]
    comms.update({"drafted": bool(draft), "draft": draft, "problems": problems, "wording": wording,
                  "probability": p, "en_count": sms.count(draft.get("sms_en", "")).as_dict() if draft else None,
                  "ta_count": sms.count(draft.get("sms_ta", "")).as_dict() if draft else None})
    ctx.emit("step", {"node": "COMMUNICATE", "status": "done", "ms": int((time.perf_counter() - t0) * 1000),
                      "public_message": bool(draft), "valid": not problems})
    return {"communications": comms, "status": "completed"}


def comms_problems(draft: dict, p: float, wording: str, en_max: int, ta_max: int, allowed: set[float]) -> list[str]:
    from app.agent.verify import number_allowed
    from app.rag.text import numbers_in

    out = sms.check_english(draft.get("sms_en", ""), en_max) + sms.check_tamil(draft.get("sms_ta", ""), ta_max)
    if wording.lower() not in (draft.get("sms_en") or "").lower():
        out.append(f"English message must contain the wording \"{wording}\" exactly.")
    if not message_mentions_probability(draft.get("sms_ta", ""), p):
        out.append(f"Tamil message must contain the probability number ({pct_int(p)}%).")
    allowed_plus = set(allowed) | {float(pct_int(p))}
    for k in ("sms_en", "sms_ta", "headline_en", "description_en", "instruction_en"):
        for x in numbers_in(draft.get(k, "")):
            if not number_allowed(x, allowed_plus):
                out.append(f"{k}: number {x:g} is not in the evidence.")
    return out


async def node_partial(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    ctx.emit("step", {"node": "PARTIAL", "status": "done",
                      "message": "Retries exhausted — partial plan with problems clearly marked; communications not drafted."})
    return {"status": "partial" if state.get("draft_plan") else "failed"}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("assess", node_assess)
    g.add_node("gather", node_gather)
    g.add_node("plan", node_plan)
    g.add_node("verify", node_verify)
    g.add_node("communicate", node_communicate)
    g.add_node("partial", node_partial)
    g.add_edge(START, "assess")
    g.add_edge("assess", "gather")
    g.add_conditional_edges("gather", route_after_gather, {"plan": "plan", "end": END})
    g.add_edge("plan", "verify")
    g.add_conditional_edges("verify", route_after_verify, {"plan": "plan", "communicate": "communicate", "partial": "partial"})
    g.add_edge("communicate", END)
    g.add_edge("partial", END)
    return g.compile()
