"""Plan documents (upload / list / chunk lookup) and Plan Q&A (F11, stretch)."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agent import prompts
from app.agent.llm import LLMRefused, LLMUnavailable, get_llm
from app.agent.verify import quote_in_chunk
from app.api.common import iso
from app.auth import User, admin_user, current_user
from app.config import get_config, get_settings
from app.db.models import Approval, PlanChunk, PlanDocument
from app.db.session import session_scope
from app.errors import APIError
from app.rag import retrieve
from app.rag.ingest import IngestError, delete_document, ingest_pdf
from app.ratelimit import limit

router = APIRouter(prefix="/api", tags=["plans"])


@router.get("/plans")
def list_plans(district_id: str | None = None):
    with session_scope() as s:
        q = select(PlanDocument).order_by(PlanDocument.ingested_at.desc())
        if district_id:
            q = q.where(PlanDocument.district_id == district_id)
        docs = s.execute(q).scalars().all()
        counts = dict(s.execute(select(PlanChunk.document_id, func.count(PlanChunk.id)).group_by(PlanChunk.document_id)).all())
        return {"documents": [{
            "id": d.id, "district_id": d.district_id, "kind": d.kind, "title": d.title, "edition_year": d.edition_year,
            "source_url": d.source_url, "sha256": d.sha256, "pages": d.pages, "chunks": counts.get(d.id, 0),
            "ocr_pages": d.ocr_pages, "low_confidence_pages": d.low_confidence_pages, "ingested_at": iso(d.ingested_at),
            "ingested_by": d.ingested_by, "embedding_backend": d.embedding_backend} for d in docs],
            "configured_sources": get_config().plans}


@router.post("/plans", dependencies=[Depends(limit(capacity=5, refill_per_s=1 / 60))])
async def upload_plan(
    file: UploadFile = File(...),
    kind: str = Form("district"),
    district_id: str | None = Form(None),
    title: str = Form(..., max_length=500),
    source_url: str | None = Form(None, max_length=2000),
    edition_year: int | None = Form(None),
    user: User = Depends(current_user),
):
    limit_bytes = get_settings().max_upload_mb * 1024 * 1024
    if district_id and get_config().district(district_id) is None:
        raise APIError(404, "unknown_district", f"Unknown district '{district_id}'.")
    if source_url and not re.match(r"^https?://", source_url):
        raise APIError(422, "bad_source_url", "source_url must be an http(s) URL of the official document.")
    if file.content_type and file.content_type not in ("application/pdf", "application/x-pdf", "application/octet-stream"):
        raise APIError(415, "not_a_pdf", f"Only PDF files are accepted (got {file.content_type}).")
    data = await file.read(limit_bytes + 1)
    if len(data) > limit_bytes:
        raise APIError(413, "file_too_large", f"File exceeds the {get_settings().max_upload_mb} MB limit.")
    if edition_year is not None and not (1990 <= edition_year <= 2100):
        raise APIError(422, "bad_year", "edition_year looks wrong.")
    try:
        import asyncio

        result = await asyncio.to_thread(ingest_pdf, data, title=title, kind=kind,
                                         district_id=district_id if kind != "generic" else None,
                                         source_url=source_url, edition_year=edition_year, ingested_by=user.name)
    except IngestError as exc:
        raise APIError(422, exc.code, exc.message) from exc
    with session_scope() as s:
        s.add(Approval(target_type="plan_document", target_id=result["document_id"], decision="upload",
                       before=None, after=title, user=user.name, role=user.role, note=source_url))
    return result


@router.delete("/plans/{doc_id}")
def remove_plan(doc_id: str, user: User = Depends(admin_user)):
    if not delete_document(doc_id):
        raise APIError(404, "not_found", "Document not found")
    with session_scope() as s:
        s.add(Approval(target_type="plan_document", target_id=doc_id, decision="delete", before=None, after=None,
                       user=user.name, role=user.role))
    return {"deleted": doc_id}


@router.get("/plans/chunks/{chunk_id:path}")
def chunk(chunk_id: str):
    c = retrieve.get_chunk(chunk_id)
    if c is None:
        raise APIError(404, "not_found", "Chunk not found")
    return c


@router.get("/plans/search")
def search(district_id: str, q: str, k: int = 6):
    if not q.strip():
        raise APIError(422, "empty_query", "Query cannot be empty.")
    scope = "generic" if district_id == "generic" else retrieve.scope_for_district(district_id)
    return retrieve.search(scope, q[:300], max(1, min(10, k))).as_dict()


class AskBody(BaseModel):
    district_id: str = Field(..., max_length=64)
    question: str = Field(..., max_length=500)


ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["chunk_id", "quote"], "additionalProperties": False}},
        "found_in_plan": {"type": "boolean"},
    },
    "required": ["answer", "citations", "found_in_plan"],
    "additionalProperties": False,
}


@router.post("/ask", dependencies=[Depends(limit(capacity=10, refill_per_s=1 / 10))])
async def ask(body: AskBody, user: User = Depends(current_user)):
    q = body.question.strip()
    if len(q) < 4:
        raise APIError(422, "empty_question", "Please ask a question about the district plan.")
    if get_config().district(body.district_id) is None:
        raise APIError(404, "unknown_district", f"Unknown district '{body.district_id}'.")
    res = retrieve.search(retrieve.scope_for_district(body.district_id), q, 6)
    if not res.found:
        return {"answer": "Not found in district plan.", "citations": [], "found_in_plan": False,
                "reason": res.reason}
    llm = get_llm()
    if llm is None:
        return {"answer": None, "citations": [], "found_in_plan": True, "llm_unavailable": True,
                "chunks": [{k: c[k] for k in ("chunk_id", "document_title", "page", "section", "text")} for c in res.chunks]}
    evidence = json.dumps([{k: c[k] for k in ("chunk_id", "document_title", "page", "section", "text")} for c in res.chunks],
                          ensure_ascii=False)
    system = prompts.SHARED + ("\n\nAnswer the officer's question ONLY from the plan chunks provided. Cite chunk_id + a verbatim "
                               "quote for every claim. If the chunks do not answer it, say 'Not found in district plan.' "
                               "and set found_in_plan false. Politely decline questions unrelated to disaster management.")
    try:
        r = await llm.chat(system=system, messages=[{"role": "user", "content": f"<evidence>\n{evidence}\n</evidence>\n\nQuestion: {q}"}],
                           output_schema=ASK_SCHEMA, max_tokens=4000, effort="medium")
    except (LLMUnavailable, LLMRefused) as exc:
        raise APIError(503, "llm_unavailable", f"Plan Q&A temporarily unavailable: {exc}", retryable=True) from exc
    out = r.parsed or {}
    by_id = {c["chunk_id"]: c for c in res.chunks}
    cites = []
    for c in out.get("citations") or []:
        ch = by_id.get(c.get("chunk_id"))
        cites.append({**c, "verified": bool(ch and quote_in_chunk(c.get("quote", ""), ch["text"])),
                      "page": ch["page"] if ch else None, "document_title": ch["document_title"] if ch else None})
    return {"answer": out.get("answer"), "citations": cites, "found_in_plan": out.get("found_in_plan", False),
            "all_citations_verified": all(c["verified"] for c in cites) if cites else False, "model": r.model}
