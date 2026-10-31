"""Feature 1: deterministic plan structuring. Page text here is synthetic, shaped like real
DDMP extraction (one table cell per line)."""
from app.plandb.extract import (
    dedupe,
    extract_contacts,
    extract_page,
    extract_procedures,
    extract_resources,
    extract_shelters,
    malformed_numbers,
    merge_chunks,
)


def lines(s: str) -> list[str]:
    return s.strip("\n").split("\n")


def test_contacts_group_label_lines_and_numbers_across_cells():
    page = lines("""1.
A. Kumar,
Tahsildar
Taluk Office
Villianur
9486446899
O: 2254449 taluk.vil@nic.in
2.
B. Devi, Commissioner
Mannadipet Commune Panchayat
2640161
9443746529""")
    recs = extract_contacts(12, page)
    assert len(recs) == 2
    a, b = recs
    assert "Tahsildar" in a.label and "A. Kumar" in a.label
    assert a.fields["phones"] == ["9486446899", "2254449"] and a.fields["emails"] == ["taluk.vil@nic.in"]
    assert a.fields["role"] == "Tahsildar" and a.fields["department"] == "Revenue"
    assert b.fields["phones"] == ["2640161", "9443746529"]  # 7-digit landline accepted next to a mobile
    assert all(q in "\n".join(page) for q in (a.quote, b.quote))  # quotes are exact page text


def test_bare_seven_digit_numbers_are_not_phones():
    assert extract_contacts(3, lines("Population of the taluk\n2254449\nArea in sq km")) == []


def test_index_of_annexures_is_not_an_inventory():
    page = lines("""17
National Disaster Response force
742
18
Rescue Boats
742
19
Transport Equipments & Lorries
744""")
    assert extract_resources(732, page) == []


def test_inventory_and_past_use_are_told_apart():
    recs = extract_resources(288, lines("JCB – 2 Nos.\n280 boats were used for rescue in 2015"))
    by_item = {r.fields["item"]: r.fields for r in recs}
    assert by_item["Earth movers / JCB"]["quantity"] == 2 and by_item["Earth movers / JCB"]["context"] == "inventory"
    assert by_item["Boats"]["quantity"] == 280 and by_item["Boats"]["context"] == "past_use"


def test_shelter_total_and_aligned_table_rows():
    page = lines("""There are 12 relief centres to a total capacity of 2880 that are readily available.
Sl. No.
Ward No.
Capacity
Name & Address
of relief centres
5
5
200
Jayagopal
Garodiya
Govt. Hr. Sec. School,
Yes
6
9
7
Misaligned Corporation School""")
    recs = extract_shelters(220, page)
    total = [r for r in recs if r.fields.get("aggregate")]
    assert total and total[0].fields == {"count": 12, "capacity": 2880, "aggregate": True}
    rows = [r for r in recs if not r.fields.get("aggregate") and r.fields.get("capacity")]
    assert [(r.label, r.fields["capacity"], r.fields["ward"]) for r in rows] == [
        ("Jayagopal Garodiya Govt. Hr. Sec. School", 200, 5)]  # the 2-number row does not align: skipped


def test_sop_headings_but_not_document_titles():
    recs = extract_procedures(5, lines("SOP FOR SEARCH AND RESCUE:\nPUDUCHERRY DISTRICT DISASTER MANAGEMENT ACTION PLAN - 2023"))
    assert [r.label for r in recs] == ["SOP FOR SEARCH AND RESCUE"]


def test_malformed_mobile_numbers_are_flagged():
    assert malformed_numbers("Mobile 944519020\nInspector 94451902066") == ["944519020", "94451902066"]
    assert malformed_numbers("Mobile 9445190206") == []


def test_merge_chunks_removes_overlap_and_dedupe_merges_repeats():
    a = "Line one of the page\nshared overlap text here"
    b = "shared overlap text here\nline three"
    assert merge_chunks([a, b]) == "Line one of the page\nshared overlap text here\nline three"
    page = "Tahsildar\n9486446899"
    assert len(dedupe(extract_page(1, page) + extract_page(2, page))) == 1
