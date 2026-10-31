"""Features 2 and 4 API: local impact areas and critical assets at risk."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.config import get_config
from app.errors import APIError
from app.impact import assets as asset_mod
from app.impact import engine
from app.ratelimit import limit

router = APIRouter(prefix="/api", tags=["impact"])


def _check(district_id: str) -> None:
    if get_config().district(district_id) is None:
        raise APIError(404, "not_found", f"Unknown district '{district_id}'")


@router.get("/districts/{district_id}/impact", dependencies=[Depends(limit(capacity=20, refill_per_s=0.5))])
async def impact(district_id: str, refresh: bool = Query(False)):
    _check(district_id)
    return await engine.get_impact(district_id, force=refresh)


@router.get("/districts/{district_id}/assets", dependencies=[Depends(limit(capacity=20, refill_per_s=0.5))])
async def critical_assets(district_id: str, refresh: bool = Query(False)):
    _check(district_id)
    return await asset_mod.get_assets(district_id, force=refresh)
