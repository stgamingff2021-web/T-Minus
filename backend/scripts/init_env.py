"""Create a ``.env`` with strong random credentials (never overwrites an existing one).

    python -m scripts.init_env                     # random SECRET_KEY, officer and admin passcodes
    python -m scripts.init_env --gemini-key KEY    # also store the Gemini key

The file is written to the repository root with owner-only permissions where the OS
supports it. ``.env`` is git-ignored; the generated passcodes are printed once as the
file path only, never to the terminal.
"""
from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from pathlib import Path

from app.config import REPO_ROOT

_ALPHABET = string.ascii_letters + string.digits


def _passcode(n: int = 18) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def render(gemini_key: str = "") -> str:
    return f"""# T-Minus local environment. Git-ignored: never commit this file.
ENV=development
SECRET_KEY={secrets.token_urlsafe(48)}

# Officer / admin sign-in: any display name plus one of these passcodes.
OFFICER_PASSCODE={_passcode()}
ADMIN_PASSCODE={_passcode()}

CORS_ORIGINS=http://localhost:5173,http://localhost:8000

# Agent LLM (Google AI Studio key). Leave empty for retrieval-only mode.
LLM_PROVIDER=auto
GEMINI_API_KEY={gemini_key}

# tfidf imports plans in seconds on a laptop.
EMBEDDING_BACKEND=tfidf
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gemini-key", default="", help="Google AI Studio API key to store in .env")
    ap.add_argument("--path", type=Path, default=REPO_ROOT / ".env")
    args = ap.parse_args(argv)
    if args.path.exists():
        print(f"{args.path} already exists - not overwriting.", file=sys.stderr)
        return 1
    args.path.write_text(render(args.gemini_key.strip()), encoding="utf-8", newline="\n")
    try:
        os.chmod(args.path, 0o600)
    except OSError:
        pass
    print(f"Wrote {args.path}. Open it to see your officer and admin passcodes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
