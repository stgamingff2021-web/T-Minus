"""Test configuration.

Environment is set BEFORE the app is imported (settings are cached). Every test that
touches the database gets a fresh SQLite file. HTTP is mocked with httpx.MockTransport
using tiny synthetic payloads shaped like the real APIs — they exist only inside
tests/ and are never shipped or displayed by the application (§10.2).
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-key-test-secret-key-0123456789")
os.environ.setdefault("OFFICER_PASSCODE", "officer-pass")
os.environ.setdefault("ADMIN_PASSCODE", "admin-pass")
os.environ.setdefault("EMBEDDING_BACKEND", "tfidf")
os.environ["ANTHROPIC_API_KEY"] = ""  # tests never call a real LLM
os.environ["GEMINI_API_KEY"] = ""
_tmp = tempfile.mkdtemp(prefix="tminus-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/boot.db")
os.environ.setdefault("DATA_DIR", _tmp)

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.db import session as db_session  # noqa: E402
from app.sources import base, registry  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_session.reset_engine(f"sqlite:///{tmp_path}/t.db")
    db_session.init_db()
    from app.scanner import sync_districts

    sync_districts()
    registry.reset()
    from app.rag import retrieve

    retrieve.invalidate()
    from app import scanner

    scanner._alerts_cache.update(at=0.0, alerts=[], ok=False, error=None, fetch_id=None)
    yield tmp_path


@pytest.fixture
def mock_http():
    """Install a MockTransport handler: mock_http(handler) where handler(request) -> httpx.Response."""
    installed = {}

    def install(handler):
        base._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        installed["c"] = base._client
        return base._client

    yield install
    base._client = None
