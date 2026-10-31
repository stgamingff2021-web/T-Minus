"""Shared machinery for every live source client (spec §8.1, §8.4).

Every outbound call goes through :func:`fetch`, which

1. honours the admin chaos switch (forced failure labelled "simulated outage");
2. retries timeouts / 5xx / 429 / connection errors with exponential backoff
   (3 attempts in total);
3. validates the response against a per-source schema (malformed => failure);
4. writes a ``source_fetches`` row with status, latency, HTTP code and the exact raw
   payload, so every number on screen is traceable to the response it came from;
5. updates the in-memory source registry that feeds the Sources panel.

A failure is never papered over: callers receive a :class:`SourceError` and decide
whether to fall back to an alternate source or show the last real value as STALE.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.config import get_settings
from app.db.models import SourceFetch, utcnow
from app.db.session import session_scope
from app.sources import registry
from app.tls import ssl_verify

log = logging.getLogger("tminus.sources")

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_client: httpx.AsyncClient | None = None


class SourceError(Exception):
    """A live source failed (after retries) or was forced to fail by the chaos switch."""

    def __init__(
        self,
        source: str,
        message: str,
        *,
        code: str = "source_error",
        retryable: bool = True,
        http_code: int | None = None,
        simulated: bool = False,
        fetch_id: str | None = None,
    ):
        super().__init__(message)
        self.source = source
        self.message = message
        self.code = code
        self.retryable = retryable
        self.http_code = http_code
        self.simulated = simulated
        self.fetch_id = fetch_id

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "source": self.source,
            "retryable": self.retryable,
            "simulated": self.simulated,
            "fetch_id": self.fetch_id,
        }


@dataclass
class FetchResult:
    fetch_id: str
    payload: Any
    latency_ms: int
    fetched_at: Any
    http_code: int | None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        s = get_settings()
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(s.http_timeout_s, connect=10.0),
            headers={"User-Agent": s.user_agent},
            follow_redirects=True,
            trust_env=True,
            verify=ssl_verify(),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _digest(payload: Any) -> tuple[str, int]:
    raw = payload if isinstance(payload, (bytes, bytearray)) else (
        payload.encode() if isinstance(payload, str) else json.dumps(payload, sort_keys=True).encode()
    )
    return hashlib.sha256(raw).hexdigest(), len(raw)


def record_fetch(
    *,
    source: str,
    url: str,
    params: dict,
    status: str,
    http_code: int | None = None,
    latency_ms: int | None = None,
    attempts: int = 1,
    payload: Any = None,
    error: str | None = None,
    simulated: bool = False,
    model_run_time=None,
    model_run_id: str | None = None,
    store_payload: bool = True,
) -> str:
    sha, nbytes = (None, None)
    if payload is not None:
        sha, nbytes = _digest(payload)
    stored = payload if (store_payload and not isinstance(payload, (bytes, bytearray))) else None
    with session_scope() as s:
        row = SourceFetch(
            source=source,
            url=url,
            request_params=params,
            status=status,
            http_code=http_code,
            latency_ms=latency_ms,
            attempts=attempts,
            raw_payload=stored,
            payload_sha256=sha,
            payload_bytes=nbytes,
            error=error,
            simulated=simulated,
            model_run_time=model_run_time,
            model_run_id=model_run_id,
        )
        s.add(row)
        s.flush()
        fid = row.id
    registry.observe(source, status=status, latency_ms=latency_ms, error=error, simulated=simulated, fetch_id=fid)
    return fid


async def fetch(
    source: str,
    url: str,
    *,
    params: dict | None = None,
    method: str = "GET",
    data: Any = None,
    json_body: Any = None,
    headers: dict | None = None,
    parse: str = "json",  # json | text
    validate: Callable[[Any], None] | None = None,
    attempts: int = 3,
    backoff_s: float = 0.6,
    store_payload: bool = True,
    timeout_s: float | None = None,
    log_params: dict | None = None,
) -> FetchResult:
    """Fetch, validate and log one live API call. Raises :class:`SourceError`."""
    params = params or {}
    shown_params = log_params if log_params is not None else {**params, **({"body": json_body} if json_body else {})}

    if registry.is_chaos(source):
        fid = record_fetch(
            source=source, url=url, params=shown_params, status="simulated_outage",
            error="Simulated outage (admin chaos switch)", simulated=True,
        )
        raise SourceError(source, "Simulated outage (admin chaos switch)", code="simulated_outage",
                          simulated=True, fetch_id=fid)

    client = get_client()
    last_err = "unknown error"
    last_code: int | None = None
    retryable = True
    t_start = time.perf_counter()
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.request(
                method, url, params=params or None, data=data, json=json_body, headers=headers,
                timeout=timeout_s,
            )
            last_code = resp.status_code
            if resp.status_code >= 400:
                body = resp.text[:300]
                last_err = f"HTTP {resp.status_code}: {body}"
                retryable = resp.status_code in RETRYABLE_STATUS
                if resp.status_code == 429:
                    # Rate limited: retrying within seconds only burns more quota (Open-Meteo
                    # minutely/daily limits). Surface it; callers decide when to try again.
                    break
                if retryable and attempt < attempts:
                    await asyncio.sleep(backoff_s * 2 ** (attempt - 1))
                    continue
                break
            payload: Any = resp.json() if parse == "json" else resp.text
            if validate is not None:
                try:
                    validate(payload)
                except (ValueError, KeyError, TypeError) as exc:
                    latency = int((time.perf_counter() - t_start) * 1000)
                    fid = record_fetch(
                        source=source, url=url, params=shown_params, status="invalid", http_code=resp.status_code,
                        latency_ms=latency, attempts=attempt, payload=payload if parse == "text" else None,
                        error=f"Malformed response rejected: {exc}",
                    )
                    raise SourceError(source, f"Malformed response rejected: {exc}", code="invalid_response",
                                      retryable=False, http_code=resp.status_code, fetch_id=fid) from exc
            latency = int((time.perf_counter() - t_start) * 1000)
            fid = record_fetch(
                source=source, url=url, params=shown_params, status="ok", http_code=resp.status_code,
                latency_ms=latency, attempts=attempt, payload=payload, store_payload=store_payload,
            )
            return FetchResult(fid, payload, latency, utcnow(), resp.status_code)
        except SourceError:
            raise
        except (httpx.TimeoutException, httpx.TransportError, json.JSONDecodeError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"[:300]
            retryable = not isinstance(exc, json.JSONDecodeError)
            if retryable and attempt < attempts:
                await asyncio.sleep(backoff_s * 2 ** (attempt - 1))
                continue
            break
    latency = int((time.perf_counter() - t_start) * 1000)
    fid = record_fetch(
        source=source, url=url, params=shown_params, status="error", http_code=last_code,
        latency_ms=latency, attempts=attempt, error=last_err,
    )
    raise SourceError(source, last_err, http_code=last_code, retryable=retryable, fetch_id=fid,
                      code="rate_limited" if last_code == 429 else "source_error")
