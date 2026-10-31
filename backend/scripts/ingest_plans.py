"""Download official plan PDFs and ingest them into the knowledge base (spec §5.2).

Examples (from backend/):
  python -m scripts.ingest_plans --from-config
  python -m scripts.ingest_plans --url https://…/Chennai_DDMP.pdf --district chennai --title "Chennai DDMP 2024" --year 2024
  python -m scripts.ingest_plans --file ~/Downloads/ddmp.pdf --district chennai --title "Chennai DDMP" --source-url https://…
  python -m scripts.ingest_plans --url https://…/flood.pdf --kind generic --title "NDMA Guidelines: Floods"

PDFs are never committed: they are fetched from their official source, hashed (SHA-256)
and ingested page by page with provenance on every chunk.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from app.config import get_config, get_settings
from app.db.session import init_db
from app.rag.ingest import IngestError, ingest_pdf


def download(url: str) -> bytes:
    s = get_settings()
    with httpx.Client(headers={"User-Agent": s.user_agent}, follow_redirects=True, timeout=180) as c:
        r = c.get(url)
        r.raise_for_status()
        if len(r.content) > s.max_upload_mb * 1024 * 1024:
            raise IngestError("file_too_large", f"{url} exceeds {s.max_upload_mb} MB")
        return r.content


def ingest_one(data: bytes, *, title: str, kind: str, district: str | None, url: str | None, year: int | None) -> None:
    res = ingest_pdf(data, title=title, kind=kind, district_id=district if kind != "generic" else None,
                     source_url=url, edition_year=year, ingested_by="ingest_plans script")
    if res.get("duplicate"):
        print(f"  = already ingested ({res['document_id']})")
    else:
        print(f"  + {res['pages']} pages -> {res['chunks']} chunks · vectors: {res['embedding_backend']}"
              f"{' · OCR pages ' + str(res['ocr_pages']) if res['ocr_pages'] else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-config", action="store_true", help="ingest every entry in config/plans.yaml with a source_url")
    ap.add_argument("--url")
    ap.add_argument("--file")
    ap.add_argument("--district")
    ap.add_argument("--kind", default="district", choices=["district", "city", "generic"])
    ap.add_argument("--title")
    ap.add_argument("--source-url")
    ap.add_argument("--year", type=int)
    a = ap.parse_args()
    init_db()
    if a.from_config:
        ok = True
        for doc in get_config().plans:
            if not doc.get("source_url"):
                print(f"- {doc['key']}: no source_url yet — copy the PDF link from {doc.get('landing_page')}")
                continue
            print(f"- {doc['key']}: {doc['source_url']}")
            try:
                ingest_one(download(doc["source_url"]), title=doc["title"], kind=doc["kind"], district=doc.get("district"),
                           url=doc["source_url"], year=doc.get("edition_year"))
            except (httpx.HTTPError, IngestError) as exc:
                ok = False
                print(f"  ! failed: {exc}")
        return 0 if ok else 1
    if not a.title or (a.kind != "generic" and not a.district):
        ap.error("--title and --district (unless --kind generic) are required")
    if a.district and get_config().district(a.district) is None:
        ap.error(f"unknown district {a.district!r}")
    if a.url:
        data, src = download(a.url), a.url
    elif a.file:
        data, src = Path(a.file).expanduser().read_bytes(), a.source_url
    else:
        ap.error("--url or --file is required")
    try:
        ingest_one(data, title=a.title, kind=a.kind, district=a.district, url=src, year=a.year)
    except IngestError as exc:
        print(f"! {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
