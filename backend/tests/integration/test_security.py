"""HTTPS enforcement, security headers and the sealed-link API."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security import CSP, SecurityMiddleware


def _app(force_https: bool, trust_proxy: bool = True) -> TestClient:
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/page")
    def page():
        return {"ok": True}

    app.add_middleware(SecurityMiddleware, force_https=force_https, trust_proxy=trust_proxy)
    return TestClient(app, base_url="http://example.org", follow_redirects=False)


def test_http_is_redirected_to_https_preserving_path_and_query():
    r = _app(True).get("/api/ping?x=1&y=2")
    assert r.status_code == 308
    assert r.headers["location"] == "https://example.org/api/ping?x=1&y=2"


def test_health_check_is_exempt_from_redirect():
    assert _app(True).get("/api/health").status_code == 200


def test_forwarded_proto_https_is_accepted_and_gets_hsts():
    r = _app(True).get("/api/ping", headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    assert "max-age=31536000" in r.headers["strict-transport-security"]


def test_forwarded_proto_ignored_when_proxy_not_trusted():
    r = _app(True, trust_proxy=False).get("/api/ping", headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 308


def test_no_redirect_and_no_hsts_in_development():
    r = _app(False).get("/page")
    assert r.status_code == 200
    assert "strict-transport-security" not in r.headers


def test_hardening_headers_and_api_no_store():
    r = _app(False).get("/api/ping")
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert "no-store" not in _app(False).get("/page").headers.get("cache-control", "")


def test_csp_forbids_inline_script_and_framing():
    assert "script-src 'self'" in CSP and "'unsafe-inline'" not in CSP.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in CSP and "object-src 'none'" in CSP


@pytest.fixture
def client(fresh_db):
    from app.main import create_app

    return TestClient(create_app())


def test_links_api_round_trip(client):
    r = client.post("/api/links/seal", json={"items": [{"kind": "district", "id": "chennai"}, {"kind": "run", "id": "run_abc"}]})
    assert r.status_code == 200
    d_tok, r_tok = r.json()["tokens"]
    assert client.post("/api/links/open", json={"kind": "district", "token": d_tok}).json() == {"id": "chennai"}
    assert client.post("/api/links/open", json={"kind": "run", "token": r_tok}).json() == {"id": "run_abc"}


def test_links_api_rejects_forged_and_cross_kind(client):
    tok = client.post("/api/links/seal", json={"items": [{"kind": "district", "id": "chennai"}]}).json()["tokens"][0]
    assert client.post("/api/links/open", json={"kind": "run", "token": tok}).status_code == 404
    bad = client.post("/api/links/open", json={"kind": "district", "token": "nope"})
    assert bad.status_code == 404 and bad.json()["error"]["code"] == "invalid_link"


def test_real_app_sends_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
