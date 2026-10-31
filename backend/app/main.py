"""T-Minus FastAPI application.

Run locally:   uvicorn app.main:app --reload   (from backend/)
Serves the API under /api and, if built, the React dashboard from frontend/dist.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import errors, notify, scheduler
from app.api import districts, execution, plandb, plans, replay, runs, system
from app.config import get_config, get_settings
from app.db.session import init_db
from app.sources import registry
from app.sources.base import close_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tminus")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.is_production and (not settings.secret_key or len(settings.secret_key) < 32
                                   or "SECRET_KEY" not in __import__("os").environ):
        raise RuntimeError("SECRET_KEY must be set (>= 32 chars) in production")
    get_config()  # fail fast on bad YAML
    init_db()
    from app.scanner import sync_districts

    sync_districts()
    registry.warm_from_db()
    if not settings.ors_api_key:
        registry.set_disabled("ors", "ORS_API_KEY not set")
    if not settings.imd_enabled:
        registry.set_disabled("imd", "IMD API needs IP whitelisting — optional, disabled")
    from app.agent.llm import get_llm

    get_llm()  # registers disabled status when no key
    notify.bind_loop(asyncio.get_running_loop())
    from app.plandb import store as plandb_store

    plandb_store.start_backfill()
    scheduler.start()
    log.info("T-Minus started: %d districts, scheduler=%s", len(get_config().districts), settings.scheduler_enabled)
    yield
    await scheduler.stop()
    await close_client()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="T-Minus API", version=system.VERSION, lifespan=lifespan,
                  description="Anticipatory-action agent for district disaster management (prototype).")
    errors.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def _catch_up(request: Request, call_next):
        if request.url.path in ("/api/districts", "/api/health"):
            scheduler.ensure_alive()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    for r in (system.router, districts.router, runs.router, plans.router, replay.router, plandb.router, execution.router):
        app.include_router(r)

    dist: Path = settings.frontend_dist
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content=errors.body("not_found", "Unknown API route"))
            candidate = dist / full_path
            if full_path and candidate.is_file() and dist in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"name": "T-Minus API", "docs": "/docs", "health": "/api/health",
                    "frontend": "not built — run `npm run build` in frontend/ or use the Vite dev server"}

    return app


app = create_app()
