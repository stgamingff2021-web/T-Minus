"""AC-14: garbage input -> clear messages, no crash."""
import httpx


async def test_garbage_input_gets_clear_errors(fresh_db):
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        tok = (await c.post("/api/auth/login", json={"name": "Officer", "passcode": "officer-pass"})).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        # wrong passcode / bad name
        r = await c.post("/api/auth/login", json={"name": "Officer", "passcode": "nope"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "bad_credentials"
        # unknown / misspelt district -> fuzzy suggestions
        r = await c.get("/api/districts/resolve", params={"q": "Kanyakumari"})
        assert r.json()["matches"][0]["id"] == "kanniyakumari"
        r = await c.get("/api/districts/atlantis")
        assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_district"
        # empty question
        r = await c.post("/api/ask", json={"district_id": "chennai", "question": "  "}, headers=H)
        assert r.status_code == 422 and r.json()["error"]["code"] == "empty_question"
        # non-PDF upload
        r = await c.post("/api/plans", headers=H, data={"kind": "district", "district_id": "chennai", "title": "x"},
                         files={"file": ("notes.txt", b"hello world", "text/plain")})
        assert r.status_code == 415 and r.json()["error"]["code"] == "not_a_pdf"
        # PDF content-type but not a PDF
        r = await c.post("/api/plans", headers=H, data={"kind": "district", "district_id": "chennai", "title": "x"},
                         files={"file": ("fake.pdf", b"not really a pdf", "application/pdf")})
        assert r.status_code == 422 and r.json()["error"]["code"] == "not_a_pdf"
        # 50 MB upload
        big = b"%PDF-1.4\n" + b"0" * (50 * 1024 * 1024)
        r = await c.post("/api/plans", headers=H, data={"kind": "district", "district_id": "chennai", "title": "x"},
                         files={"file": ("big.pdf", big, "application/pdf")})
        assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"
        # HTML in an action edit is refused; publishing without login is refused
        r = await c.post("/api/communications/nope/approve", json={"confirmation_token": "1.abcdefghij"})
        assert r.status_code == 401
        # malformed body -> shared error shape
        r = await c.post("/api/districts/chennai/plan", json={"hazard": "earthquake"}, headers=H)
        assert r.status_code == 422 and "error" in r.json()
