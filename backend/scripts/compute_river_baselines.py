"""Fetch GloFAS reanalysis once per river point and store P95/P99 with provenance (spec §6.4).

Usage (from backend/):  python -m scripts.compute_river_baselines
"""
from __future__ import annotations

import asyncio
import json

from app.db.session import init_db
from app.scanner import ensure_river_baselines, sync_districts
from app.sources.base import close_client


async def run() -> dict:
    try:
        return await ensure_river_baselines(wait_on_minutely=True)
    finally:
        await close_client()


def main() -> int:
    init_db()
    sync_districts()
    res = asyncio.run(run())
    print(json.dumps(res, indent=2))
    return 0 if not res["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
