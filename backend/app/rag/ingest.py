"""Plan ingestion: download/upload -> SHA-256 -> text by page -> OCR fallback -> chunks -> vectors.

For every chunk we keep document title, edition year, source URL, file SHA-256, page
number and section heading (spec §5.2). Pages without a text layer are OCR'd with
Tesseract when it is installed; low-confidence OCR pages are flagged for review.
Uploads are validated: PDF magic bytes, size limit, not encrypted, sane page count.
"""
from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.config import get_settings
from app.db.models import PlanChunk, PlanDocument, new_id
from app.db.session import session_scope
from app.rag import embed, retrieve
from app.rag.chunker import chunk_pages

log = logging.getLogger("tminus.ingest")
MAX_PAGES = 1500
OCR_MIN_CHARS = 40


class IngestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PageText:
    page: int
    text: str
    ocr: bool = False
    ocr_confidence: float | None = None


def validate_pdf_bytes(data: bytes, limit_mb: int | None = None) -> None:
    limit = (limit_mb or get_settings().max_upload_mb) * 1024 * 1024
    if not data:
        raise IngestError("empty_file", "The file is empty.")
    if len(data) > limit:
        raise IngestError("file_too_large", f"File is {len(data) / 1e6:.1f} MB; the limit is {limit // (1024 * 1024)} MB.")
    if not data[:1024].lstrip().startswith(b"%PDF"):
        raise IngestError("not_a_pdf", "Only PDF files are accepted (missing %PDF header).")


def _ocr_page(page) -> tuple[str, float | None]:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return "", None
    try:
        pix = page.get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        data = pytesseract.image_to_data(img, lang="eng", output_type=pytesseract.Output.DICT)
        words = [w for w, c in zip(data["text"], data["conf"]) if w.strip() and float(c) >= 0]
        confs = [float(c) for w, c in zip(data["text"], data["conf"]) if w.strip() and float(c) >= 0]
        text = pytesseract.image_to_string(img, lang="eng")
        return text, (sum(confs) / len(confs) if confs else None) if words else None
    except Exception as exc:  # noqa: BLE001 - OCR is best-effort; page is flagged instead
        log.warning("OCR failed on page %s: %s", page.number + 1, exc)
        return "", None


def extract_pages(data: bytes) -> list[PageText]:
    import pymupdf as fitz  # PyMuPDF

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise IngestError("unreadable_pdf", f"Could not parse the PDF safely: {exc}") from exc
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise IngestError("encrypted_pdf", "Encrypted / password-protected PDFs are not accepted.")
        if doc.page_count == 0:
            raise IngestError("empty_pdf", "The PDF has no pages.")
        if doc.page_count > MAX_PAGES:
            raise IngestError("too_many_pages", f"The PDF has {doc.page_count} pages (limit {MAX_PAGES}).")
        out = []
        for i, page in enumerate(doc):
            text = page.get_text("text") or ""
            if len(text.strip()) >= OCR_MIN_CHARS:
                out.append(PageText(i + 1, text))
                continue
            ocr_text, conf = _ocr_page(page)
            if ocr_text.strip():
                out.append(PageText(i + 1, ocr_text, ocr=True, ocr_confidence=conf))
            else:
                out.append(PageText(i + 1, text, ocr=False, ocr_confidence=None))
        return out
    finally:
        doc.close()


def ingest_pdf(
    data: bytes,
    *,
    title: str,
    kind: str,
    district_id: str | None,
    source_url: str | None,
    edition_year: int | None = None,
    ingested_by: str | None = None,
    limit_mb: int | None = None,
) -> dict:
    if kind not in ("district", "city", "generic"):
        raise IngestError("bad_kind", "kind must be district, city or generic")
    if kind != "generic" and not district_id:
        raise IngestError("missing_district", "A district is required for district/city plans.")
    validate_pdf_bytes(data, limit_mb)
    sha = hashlib.sha256(data).hexdigest()
    with session_scope() as s:
        dup = s.execute(select(PlanDocument).where(PlanDocument.sha256 == sha,
                                                   PlanDocument.district_id == district_id)).scalars().first()
        if dup:
            return {"document_id": dup.id, "duplicate": True, "pages": dup.pages, "title": dup.title}
    pages = extract_pages(data)
    chunks = chunk_pages([(p.page, p.text) for p in pages])
    if not chunks:
        raise IngestError("no_text", "No extractable text (and OCR unavailable or empty). Install Tesseract to OCR scanned plans.")
    ocr_pages = {p.page for p in pages if p.ocr}
    low_conf = sorted(p.page for p in pages if p.ocr and (p.ocr_confidence or 0) < 70)
    vectors = embed.embed_texts([c.text for c in chunks])
    backend = "fastembed" if vectors is not None else "tfidf"
    doc_id = new_id("doc")
    with session_scope() as s:
        s.add(PlanDocument(
            id=doc_id, district_id=district_id, kind=kind, title=title.strip()[:500], edition_year=edition_year,
            source_url=source_url, sha256=sha, pages=len(pages), ocr_pages=sorted(ocr_pages),
            low_confidence_pages=low_conf, ingested_by=ingested_by, embedding_backend=backend,
        ))
        s.flush()
        for i, c in enumerate(chunks):
            s.add(PlanChunk(
                id=f"{doc_id}:p{c.page}:c{c.index}", document_id=doc_id, page=c.page, section=(c.section or None),
                text=c.text, ocr=c.page in ocr_pages,
                embedding=embed.to_bytes(vectors[i]) if vectors is not None else None,
            ))
    retrieve.invalidate("generic" if kind == "generic" else retrieve.scope_for_district(district_id or ""))
    try:
        from app.plandb.store import build_document

        build_document(doc_id)
    except Exception:  # noqa: BLE001 - structuring is best-effort; the plan is ingested either way
        log.exception("structuring %s failed", doc_id)
    return {
        "document_id": doc_id, "duplicate": False, "title": title, "pages": len(pages), "chunks": len(chunks),
        "sha256": sha, "ocr_pages": sorted(ocr_pages), "low_confidence_pages": low_conf,
        "embedding_backend": backend, "district_id": district_id, "kind": kind,
    }


def delete_document(doc_id: str) -> bool:
    with session_scope() as s:
        doc = s.get(PlanDocument, doc_id)
        if not doc:
            return False
        scope = "generic" if doc.kind == "generic" else retrieve.scope_for_district(doc.district_id or "")
        for c in s.execute(select(PlanChunk).where(PlanChunk.document_id == doc_id)).scalars():
            s.delete(c)
        s.delete(doc)
    retrieve.invalidate(scope)
    return True
