"""One error shape for every endpoint (spec §13):

    { "error": { "code": "...", "message": "...", "source": "...", "retryable": true } }
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("tminus.api")


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, *, source: str | None = None, retryable: bool = False,
                 details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.source = source
        self.retryable = retryable
        self.details = details


def body(code: str, message: str, source: str | None = None, retryable: bool = False, details: dict | None = None) -> dict:
    err = {"code": code, "message": message, "source": source, "retryable": retryable}
    if details:
        err["details"] = details
    return {"error": err}


def install(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError):
        return JSONResponse(status_code=exc.status, content=body(exc.code, exc.message, exc.source, exc.retryable, exc.details))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized", 403: "forbidden"}.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=body(code, str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        msgs = []
        for e in exc.errors()[:5]:
            loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
            msgs.append(f"{loc}: {e.get('msg')}" if loc else e.get("msg"))
        return JSONResponse(status_code=422, content=body("invalid_input", "; ".join(msgs) or "Invalid input"))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content=body("internal_error", "Unexpected server error; it has been logged.",
                                                          retryable=True))
