"""Feature 1 API: the district plan as structured, searchable data, plus its gaps."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.config import get_config
from app.errors import APIError
from app.plandb import store
from app.plandb.extract import KINDS

router = APIRouter(prefix="/api", tags=["plan data"])


def _district(district_id: str):
    d = get_config().district(district_id)
    if d is None:
        raise APIError(404, "not_found", f"Unknown district '{district_id}'")
    return d


@router.get("/districts/{district_id}/plan-data")
def plan_data(district_id: str, kind: str | None = Query(None, max_length=16), q: str | None = Query(None, max_length=120),
              limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
    _district(district_id)
    if kind and kind not in KINDS:
        raise APIError(400, "bad_kind", f"kind must be one of {', '.join(KINDS)}")
    return store.records(district_id, kind=kind, q=q, limit=limit, offset=offset)


@router.get("/districts/{district_id}/plan-gaps")
def plan_gaps(district_id: str):
    _district(district_id)
    return {"gaps": store.gaps(district_id)}


@router.get("/plans/{doc_id}/pages/{page}")
def plan_page(doc_id: str, page: int):
    p = store.page_text(doc_id, page)
    if p is None:
        raise APIError(404, "not_found", "Page not found")
    return p
