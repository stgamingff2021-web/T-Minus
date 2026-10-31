"""Setup script: `ingest_plans --from-config` skips documents already ingested from the same
URL (no re-download on reruns), re-downloads with --force, and explains certificate failures."""
import ssl
import sys
from types import SimpleNamespace

import httpx
import pymupdf as fitz

from app.rag.ingest import ingest_pdf
from scripts import ingest_plans

URL = "https://example.gov.in/ddmp.pdf"


def make_pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


PDF = make_pdf("CHAPTER 4 EVACUATION\nThe Tahsildar shall evacuate residents of low-lying areas to relief camps.")
DOC = {"key": "t", "kind": "district", "district": "chennai", "title": "Test DDMP", "source_url": URL, "edition_year": 2024}


def run(monkeypatch, *args):
    monkeypatch.setattr(ingest_plans, "get_config", lambda: SimpleNamespace(plans=[DOC]))
    monkeypatch.setattr(sys, "argv", ["ingest_plans", "--from-config", *args])
    return ingest_plans.main()


def test_rerun_skips_already_ingested_url_without_downloading(fresh_db, monkeypatch, capsys):
    res = ingest_pdf(PDF, title="Test DDMP", kind="district", district_id="chennai", source_url=URL)
    assert ingest_plans.already_ingested(URL) == res["document_id"]
    assert ingest_plans.already_ingested("https://example.gov.in/other.pdf") is None

    def no_download(url):
        raise AssertionError("must not download an already-ingested document")

    monkeypatch.setattr(ingest_plans, "download", no_download)
    assert run(monkeypatch) == 0
    assert "already ingested" in capsys.readouterr().out


def test_force_redownloads(fresh_db, monkeypatch, capsys):
    ingest_pdf(PDF, title="Test DDMP", kind="district", district_id="chennai", source_url=URL)
    calls = []
    monkeypatch.setattr(ingest_plans, "download", lambda url: calls.append(url) or PDF)
    assert run(monkeypatch, "--force") == 0
    assert calls == [URL]  # downloaded again; same SHA-256 -> stored once


def test_certificate_failure_is_explained_and_not_bypassed(fresh_db, monkeypatch, capsys):
    def cert_fail(url):
        try:
            raise ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")
        except ssl.SSLError as inner:
            raise httpx.ConnectError(str(inner)) from inner

    monkeypatch.setattr(ingest_plans, "download", cert_fail)
    assert run(monkeypatch) == 1
    out = capsys.readouterr().out
    assert "could not be verified" in out and "Plans page" in out
    assert ingest_plans.already_ingested(URL) is None
