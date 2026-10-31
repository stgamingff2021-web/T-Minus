"""In-memory token-bucket rate limit for write endpoints (spec §9.3)."""
from __future__ import annotations

import threading
import time

from fastapi import Request

from app.errors import APIError

_lock = threading.Lock()
_buckets: dict[str, tuple[float, float]] = {}


def limit(capacity: int = 30, refill_per_s: float = 0.5):
    """FastAPI dependency: ``capacity`` burst, refilled at ``refill_per_s`` tokens/s per client IP."""

    def dep(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        key = f"{request.url.path.split('/')[2] if request.url.path.count('/') > 1 else ''}:{ip}"
        now = time.monotonic()
        with _lock:
            tokens, last = _buckets.get(key, (float(capacity), now))
            tokens = min(capacity, tokens + (now - last) * refill_per_s)
            if tokens < 1:
                raise APIError(429, "rate_limited", "Too many requests — slow down and retry shortly.", retryable=True)
            _buckets[key] = (tokens - 1, now)

    return dep
