"""Opaque, tamper-proof identifiers for page URLs.

A browser address such as ``/district/chennai`` or ``/run/run_3fa1…`` reveals internal
identifiers, can be guessed and can be edited by hand. Instead the dashboard puts a
*sealed token* in the address bar: the identifier is encrypted and authenticated with
AES-SIV (RFC 5297) under a key derived from ``SECRET_KEY``.

Properties that matter here:

* Deterministic: the same id always yields the same token, so links can be bookmarked,
  shared and cached. (A randomised cipher would give a new URL on every render.)
* Authenticated: a token that was altered, forged or made under another key fails to open.
* Bound to a kind: the ``kind`` is authenticated data, so a district token cannot be
  replayed as a run token.

This is obfuscation of identifiers at the address-bar level, not access control. Read
endpoints remain public by design (spec §9.3); every write still needs an officer
session. Transport encryption of the whole URL, path and query included, is HTTPS, which
``app/security.py`` enforces in production.
"""
from __future__ import annotations

import base64
import functools
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import get_settings
from app.errors import APIError
from app.ratelimit import limit

KINDS = ("district", "run")
_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,96}$")
_INFO = b"tminus/url-token/v1"


@functools.lru_cache(maxsize=4)
def _cipher_for(secret: str) -> AESSIV:
    key = HKDF(algorithm=hashes.SHA256(), length=64, salt=None, info=_INFO).derive(secret.encode())
    return AESSIV(key)


def _cipher() -> AESSIV:
    return _cipher_for(get_settings().secret_key)


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise APIError(422, "invalid_kind", f"kind must be one of: {', '.join(KINDS)}.")


def seal(kind: str, ident: str) -> str:
    """Encrypt ``ident`` into a URL-safe token bound to ``kind``."""
    _check_kind(kind)
    if not _ID_RE.match(ident or ""):
        raise APIError(422, "invalid_id", "Identifier has unsupported characters or length.")
    blob = _cipher().encrypt(ident.encode(), [kind.encode()])
    return base64.urlsafe_b64encode(blob).decode().rstrip("=")


def unseal(kind: str, token: str) -> str:
    """Return the identifier inside ``token`` or raise a 404 ``invalid_link``."""
    _check_kind(kind)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        ident = _cipher().decrypt(raw, [kind.encode()]).decode()
    except (InvalidTag, ValueError, UnicodeDecodeError):
        raise APIError(404, "invalid_link", "This link is not valid. It may have been altered or issued by another server.") from None
    return ident


# --------------------------------------------------------------------------- API

router = APIRouter(prefix="/api/links", tags=["links"])


class SealItem(BaseModel):
    kind: str = Field(..., max_length=16)
    id: str = Field(..., max_length=96)


class SealBody(BaseModel):
    items: list[SealItem] = Field(..., min_length=1, max_length=200)


class OpenBody(BaseModel):
    kind: str = Field(..., max_length=16)
    token: str = Field(..., max_length=256)


@router.post("/seal", dependencies=[Depends(limit(capacity=60, refill_per_s=5))])
def seal_many(body: SealBody):
    return {"tokens": [seal(i.kind, i.id) for i in body.items]}


@router.post("/open", dependencies=[Depends(limit(capacity=120, refill_per_s=10))])
def open_one(body: OpenBody):
    return {"id": unseal(body.kind, body.token)}
