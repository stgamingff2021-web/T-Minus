"""Transport security and browser hardening.

* **HTTPS enforcement** — in production every plain-HTTP request is redirected (308) to
  HTTPS, so URLs, paths, query strings, tokens and bodies only ever travel encrypted.
  Behind a TLS-terminating proxy (Render, Railway, Fly, Hugging Face) the original scheme
  is read from ``X-Forwarded-Proto``; set ``TRUST_PROXY_HEADERS=false`` when the app is
  exposed directly. ``/api/health`` is exempt so platform health checks keep working.
* **HSTS** — once a response was served over HTTPS the browser is told to refuse plain
  HTTP for this host for a year.
* **Content-Security-Policy and friends** — the dashboard loads only its own scripts and
  styles. The one external origin is the CARTO basemap (tiles, style and glyphs).
* **No caching of API responses** — they carry live hazard data and, for officers,
  session-bound content.

Implemented as a pure ASGI middleware so streaming responses (SSE) are not buffered.
"""
from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_BASEMAP = "https://*.basemaps.cartocdn.com https://basemaps.cartocdn.com"

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",          # MapLibre sets inline styles at runtime
    f"img-src 'self' data: blob: {_BASEMAP}",
    "font-src 'self' data:",
    f"connect-src 'self' {_BASEMAP}",
    "worker-src 'self' blob:",
    "child-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

_STATIC_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# Swagger UI (/docs) needs a CDN script and inline JS; it is a developer aid, so it gets a relaxed policy.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def is_secure(scope: Scope, trust_proxy: bool) -> bool:
    if scope.get("scheme") in ("https", "wss"):
        return True
    if trust_proxy:
        fwd = Headers(scope=scope).get("x-forwarded-proto", "")
        return fwd.split(",")[0].strip().lower() == "https"
    return False


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, force_https: bool, trust_proxy: bool, hsts_max_age: int = 31_536_000) -> None:
        self.app = app
        self.force_https = force_https
        self.trust_proxy = trust_proxy
        self.hsts = f"max-age={hsts_max_age}; includeSubDomains"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        secure = is_secure(scope, self.trust_proxy)
        path: str = scope.get("path", "")

        if self.force_https and not secure and path != "/api/health":
            host = Headers(scope=scope).get("host", "")
            if not host:
                await PlainTextResponse("HTTPS required", status_code=400)(scope, receive, send)
                return
            query = scope.get("query_string", b"").decode("latin-1")
            target = f"https://{host}{scope.get('raw_path', path.encode()).decode('latin-1')}"
            if query:
                target += f"?{query}"
            await RedirectResponse(target, status_code=308)(scope, receive, send)
            return

        relaxed = path.startswith(_DOCS_PATHS)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in _STATIC_HEADERS.items():
                    if relaxed and k == "Content-Security-Policy":
                        continue
                    headers.setdefault(k, v)
                if secure:
                    headers.setdefault("Strict-Transport-Security", self.hsts)
                if path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrapper)
