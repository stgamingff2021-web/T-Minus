"""Verify the committed CAP 1.2 schema against the official OASIS copy.

The repository ships the OASIS CAP v1.2 XSD (the only data-like file allowed besides
configuration, spec §9.1). This script downloads the official file and compares its
SHA-256 with the committed one; with --write it replaces the local copy.

Usage (from backend/):  python -m scripts.fetch_cap_xsd [--write]
"""
from __future__ import annotations

import argparse
import hashlib

import httpx

from app.comms.cap import XSD_PATH

OFFICIAL = "https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2.xsd"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    local = XSD_PATH.read_bytes()
    r = httpx.get(OFFICIAL, timeout=60, follow_redirects=True)
    r.raise_for_status()
    remote = r.content
    lh, rh = hashlib.sha256(local).hexdigest(), hashlib.sha256(remote).hexdigest()
    norm = lambda b: b"".join(b.split())  # noqa: E731 - whitespace-insensitive comparison
    print(f"local  {lh}\nOASIS  {rh}")
    if lh == rh:
        print("identical")
    elif norm(local) == norm(remote):
        print("identical apart from whitespace/line endings")
    else:
        print("DIFFERENT")
        if a.write:
            XSD_PATH.write_bytes(remote)
            print(f"replaced {XSD_PATH}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
