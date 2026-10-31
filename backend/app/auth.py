"""Officer login (spec §9.3): display name + role only, no personal data.

Two shared passcodes from the environment (``OFFICER_PASSCODE``, ``ADMIN_PASSCODE``)
map to the roles ``officer`` and ``admin``. A successful login returns an HMAC-signed
token (12 h). Write endpoints require a token; the chaos switch requires ``admin``.
The public alerts page and all read endpoints stay open (read-only).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass

from fastapi import Depends, Header

from app.config import get_settings
from app.errors import APIError

TOKEN_TTL_S = 12 * 3600
_NAME_RE = re.compile(r"^[\w .,'()-]{2,64}$", re.UNICODE)


@dataclass
class User:
    name: str
    role: str  # officer | admin


def _sign(payload: bytes) -> str:
    key = get_settings().secret_key.encode()
    return base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode().rstrip("=")


def issue_token(name: str, role: str) -> str:
    payload = json.dumps({"n": name, "r": role, "exp": int(time.time()) + TOKEN_TTL_S}, separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{b}.{_sign(payload)}"


def parse_token(token: str) -> User | None:
    try:
        b, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(payload)
        if data.get("exp", 0) < time.time():
            return None
        return User(name=data["n"], role=data["r"])
    except Exception:  # noqa: BLE001 - any malformed token is simply invalid
        return None


def login(name: str, passcode: str) -> User:
    s = get_settings()
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise APIError(422, "invalid_name", "Display name must be 2–64 letters, digits, spaces or basic punctuation.")
    if not s.officer_passcode and not s.admin_passcode:
        raise APIError(503, "login_not_configured",
                       "Officer login is not configured: set OFFICER_PASSCODE and ADMIN_PASSCODE in the environment.")
    if s.admin_passcode and hmac.compare_digest(passcode.encode(), s.admin_passcode.encode()):
        return User(name, "admin")
    if s.officer_passcode and hmac.compare_digest(passcode.encode(), s.officer_passcode.encode()):
        return User(name, "officer")
    raise APIError(401, "bad_credentials", "Wrong passcode.")


def current_user(authorization: str | None = Header(default=None)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise APIError(401, "login_required", "Officer login required for this action.")
    user = parse_token(authorization.split(" ", 1)[1].strip())
    if user is None:
        raise APIError(401, "session_expired", "Session invalid or expired — please log in again.")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise APIError(403, "admin_only", "Only an admin can use the chaos switch.")
    return user


def optional_user(authorization: str | None = Header(default=None)) -> User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return parse_token(authorization.split(" ", 1)[1].strip())
