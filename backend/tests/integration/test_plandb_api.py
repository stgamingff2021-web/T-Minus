"""Feature 1 end to end: ingesting a plan structures it; the API serves records, search,
page text and gaps (outdated edition, missing lists)."""
import pymupdf as fitz
from fastapi.testclient import TestClient

from app.main import create_app
from app.rag.ingest import ingest_pdf


def make_pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


PAGES = [
    "CONTACTS\nA. Kumar,\nTahsildar\nTaluk Office\n9486446899\nR. Devi\nInspector of Police\n9443746529",
    "RELIEF CENTRES\nThere are 12 relief centres to a total capacity of 2880 in the district.\n"
    "SOP FOR SEARCH AND RESCUE:\nThe Police shall deploy rescue teams to low lying areas.",
]


def test_ingest_structures_the_plan_and_reports_gaps(fresh_db):
    res = ingest_pdf(make_pdf(PAGES), title="Test DDMP", kind="district", district_id="chennai",
                     source_url="https://example.gov.in/ddmp.pdf", edition_year=2020)
    c = TestClient(create_app())
    data = c.get("/api/districts/chennai/plan-data").json()
    assert data["counts"]["contact"] == 2 and data["counts"]["shelter"] == 1 and data["counts"]["procedure"] == 1
    assert data["documents"][0]["structured"] is True

    found = c.get("/api/districts/chennai/plan-data", params={"kind": "contact", "q": "Tahsildar"}).json()
    assert found["total"] == 1 and found["records"][0]["fields"]["phones"] == ["9486446899"]
    rec = found["records"][0]
    page = c.get(f"/api/plans/{res['document_id']}/pages/{rec['page']}").json()
    assert rec["quote"] in page["text"]

    codes = {g["code"] for g in c.get("/api/districts/chennai/plan-gaps").json()["gaps"]}
    assert {"outdated", "no_inventory"} <= codes
    assert "no_shelters" not in codes and "no_contacts" not in codes

    assert c.get("/api/districts/chennai/plan-data", params={"kind": "bogus"}).status_code == 400
    assert c.get("/api/districts/nowhere/plan-gaps").status_code == 404


def test_district_without_a_plan_has_one_gap(fresh_db):
    gaps = TestClient(create_app()).get("/api/districts/ariyalur/plan-gaps").json()["gaps"]
    assert [g["code"] for g in gaps] == ["no_plan"]
