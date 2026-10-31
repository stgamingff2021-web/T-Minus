"""Feature 1 storage: build, backfill, search and gap-check the structured plan data."""
from __future__ import annotations

import collections
import datetime as dt
import logging
import threading

from sqlalchemy import delete, func, or_, select

from app.db.models import PlanChunk, PlanDocument, PlanRecord, PlanStructure, utcnow
from app.db.session import session_scope
from app.plandb.extract import EXTRACTOR_VERSION, KINDS, dedupe, extract_page, malformed_numbers, merge_chunks

log = logging.getLogger("tminus.plandb")
_lock = threading.Lock()


def _chunk_index(chunk_id: str) -> int:
    try:
        return int(chunk_id.rsplit(":c", 1)[1])
    except (IndexError, ValueError):
        return 0


def _page_texts(s, document_id: str, page: int | None = None) -> dict[int, str]:
    q = select(PlanChunk.page, PlanChunk.id, PlanChunk.text).where(PlanChunk.document_id == document_id)
    if page is not None:
        q = q.where(PlanChunk.page == page)
    by_page: dict[int, list] = collections.defaultdict(list)
    for p, cid, text in s.execute(q).all():
        by_page[p].append((_chunk_index(cid), text))
    return {p: merge_chunks([t for _, t in sorted(v)]) for p, v in by_page.items()}


def page_text(document_id: str, page: int) -> dict | None:
    with session_scope() as s:
        doc = s.get(PlanDocument, document_id)
        if doc is None:
            return None
        text = _page_texts(s, document_id, page).get(page)
        if text is None:
            return None
        return {"document_id": doc.id, "title": doc.title, "edition_year": doc.edition_year,
                "source_url": doc.source_url, "page": page, "text": text}


def build_document(document_id: str) -> dict[str, int]:
    with _lock, session_scope() as s:
        doc = s.get(PlanDocument, document_id)
        if doc is None:
            return {}
        texts = _page_texts(s, document_id)
        recs, bad = [], []
        for p in sorted(texts):
            recs += extract_page(p, texts[p])
            bad += [{"page": p, "number": n} for n in malformed_numbers(texts[p])]
        recs = dedupe(recs)
        s.execute(delete(PlanRecord).where(PlanRecord.document_id == document_id))
        for r in recs:
            s.add(PlanRecord(document_id=document_id, district_id=doc.district_id, kind=r.kind, label=r.label[:2000],
                             fields=r.fields, page=r.page, quote=r.quote[:4000]))
        counts = dict(collections.Counter(r.kind for r in recs))
        s.merge(PlanStructure(document_id=document_id, version=EXTRACTOR_VERSION, extracted_at=utcnow(),
                              counts=counts, malformed_numbers=bad[:200]))
    log.info("structured %s: %s", document_id, counts)
    return counts


def backfill() -> list[str]:
    """Structure every document not yet structured by the current extractor version."""
    with session_scope() as s:
        done = {d for d, v in s.execute(select(PlanStructure.document_id, PlanStructure.version)) if v == EXTRACTOR_VERSION}
        todo = [d for (d,) in s.execute(select(PlanDocument.id)) if d not in done]
    for d in todo:
        try:
            build_document(d)
        except Exception:  # noqa: BLE001 - one bad document must not stop the others
            log.exception("structuring %s failed", d)
    return todo


def start_backfill() -> None:
    threading.Thread(target=backfill, daemon=True, name="plandb-backfill").start()


def remove_document(document_id: str) -> None:
    with session_scope() as s:
        s.execute(delete(PlanRecord).where(PlanRecord.document_id == document_id))
        s.execute(delete(PlanStructure).where(PlanStructure.document_id == document_id))


def _docs(s, district_id: str) -> list[PlanDocument]:
    return list(s.execute(select(PlanDocument).where(PlanDocument.district_id == district_id,
                                                     PlanDocument.kind.in_(("district", "city")))).scalars())


def record_dict(r: PlanRecord, titles: dict[str, str]) -> dict:
    return {"id": r.id, "kind": r.kind, "label": r.label, "fields": r.fields, "page": r.page, "quote": r.quote,
            "document_id": r.document_id, "document_title": titles.get(r.document_id)}


def records(district_id: str, kind: str | None = None, q: str | None = None, limit: int = 200,
            offset: int = 0) -> dict:
    with session_scope() as s:
        docs = _docs(s, district_id)
        titles = {d.id: d.title for d in docs}
        ids = list(titles)
        counts = dict(s.execute(select(PlanRecord.kind, func.count()).where(PlanRecord.document_id.in_(ids))
                                .group_by(PlanRecord.kind)).all()) if ids else {}
        query = select(PlanRecord).where(PlanRecord.document_id.in_(ids))
        if kind:
            query = query.where(PlanRecord.kind == kind)
        if q:
            like = f"%{q.strip()}%"
            query = query.where(or_(PlanRecord.label.ilike(like), PlanRecord.quote.ilike(like)))
        total = s.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
        rows = s.execute(query.order_by(PlanRecord.kind, PlanRecord.page, PlanRecord.id).limit(limit).offset(offset)).scalars()
        structured = {st.document_id: st for st in s.execute(select(PlanStructure).where(PlanStructure.document_id.in_(ids))).scalars()} if ids else {}
        return {
            "counts": {k: counts.get(k, 0) for k in KINDS},
            "total": total,
            "records": [record_dict(r, titles) for r in rows],
            "documents": [{"id": d.id, "title": d.title, "edition_year": d.edition_year, "pages": d.pages,
                           "structured": d.id in structured} for d in docs],
        }


def district_records(district_id: str, kinds: tuple[str, ...]) -> list[dict]:
    """All records of the given kinds for a district (used by readiness and asset checks)."""
    with session_scope() as s:
        docs = _docs(s, district_id)
        titles = {d.id: d.title for d in docs}
        if not titles:
            return []
        rows = s.execute(select(PlanRecord).where(PlanRecord.document_id.in_(list(titles)),
                                                  PlanRecord.kind.in_(kinds))).scalars()
        out = [record_dict(r, titles) for r in rows]
        years = {d.id: d.edition_year for d in docs}
        for r in out:
            r["edition_year"] = years.get(r["document_id"])
        return out


def plan_age_years(edition_year: int | None, now: dt.datetime | None = None) -> int | None:
    if edition_year is None:
        return None
    return (now or utcnow()).year - int(edition_year)


def gaps(district_id: str, now: dt.datetime | None = None) -> list[dict]:
    now = now or utcnow()
    out: list[dict] = []

    def gap(severity: str, code: str, message: str, pages: list[int] | None = None, document_id: str | None = None):
        out.append({"severity": severity, "code": code, "message": message, "pages": sorted(set(pages or []))[:20],
                    "document_id": document_id})

    with session_scope() as s:
        docs = _docs(s, district_id)
        if not docs:
            gap("high", "no_plan", "No district plan in the knowledge base.")
            return out
        ids = [d.id for d in docs]
        structured = {st.document_id: st for st in s.execute(select(PlanStructure).where(PlanStructure.document_id.in_(ids))).scalars()}
        recs = list(s.execute(select(PlanRecord).where(PlanRecord.document_id.in_(ids))).scalars())
        for d in docs:
            if d.id not in structured:
                gap("low", "not_structured", f"{d.title}: being indexed.", document_id=d.id)
            age = plan_age_years(d.edition_year, now)
            if age is None:
                gap("medium", "edition_unknown", f"{d.title}: edition year unknown.", document_id=d.id)
            elif age >= 2:
                gap("high", "outdated", f"{d.title}: edition {d.edition_year} — not updated in the last year "
                    "(annual review: DM Act 2005, s.31(4)).", document_id=d.id)
            bad = (structured.get(d.id).malformed_numbers if d.id in structured else []) or []
            if bad:
                gap("medium", "malformed_numbers", f"{len(bad)} phone number(s) with 9 or 11 digits.",
                    [b["page"] for b in bad], d.id)
    if not structured:
        return out
    by_kind = collections.defaultdict(list)
    for r in recs:
        by_kind[r.kind].append(r)
    if not by_kind["shelter"]:
        gap("high", "no_shelters", "No shelter or relief centre list found.")
    else:
        listed = [r for r in by_kind["shelter"] if not r.fields.get("aggregate")]
        no_cap = [r for r in listed if not r.fields.get("capacity")]
        if listed and no_cap:
            gap("medium", "shelter_capacity", f"{len(no_cap)} of {len(listed)} listed shelters have no capacity.",
                [r.page for r in no_cap])
    if not by_kind["contact"]:
        gap("high", "no_contacts", "No contact numbers found.")
    if not [r for r in by_kind["resource"] if r.fields.get("context") == "inventory"]:
        gap("medium", "no_inventory", "No equipment or vehicle inventory found.")
    if not by_kind["responsibility"]:
        gap("medium", "no_responsibilities", "No department responsibilities found.")
    if not by_kind["procedure"]:
        gap("low", "no_sops", "No SOPs found.")
    with_duties = {r.fields.get("department") for r in by_kind["responsibility"]} - {None}
    with_contacts = {r.fields.get("department") for r in by_kind["contact"]} - {None}
    missing = sorted(with_duties - with_contacts)
    if missing:
        gap("low", "no_department_contact", "Duties but no contact for: " + ", ".join(missing) + ".")
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda g: order[g["severity"]])
    return out
