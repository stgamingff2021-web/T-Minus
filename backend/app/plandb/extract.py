"""Feature 1 — the district plan as a structured, searchable database.

Deterministic extraction from ingested plan pages (no LLM): contacts, shelters / relief
centres, equipment and vehicles, department responsibilities, SOPs and critical assets
named in the plan. Every record keeps its page and an exact quote from that page, and
every number in it is parsed from that quote — nothing is inferred.

DDMP tables come out of PDF text extraction one cell per line, so the parsers work on
small windows of lines and favour precision: a capacity or quantity is recorded only when
its context makes the meaning unambiguous; otherwise the record is kept without the
number and the gap check reports it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EXTRACTOR_VERSION = 1
KINDS = ("contact", "shelter", "resource", "responsibility", "procedure", "asset")


@dataclass
class Record:
    kind: str
    label: str
    page: int
    quote: str
    fields: dict = field(default_factory=dict)


# ----------------------------------------------------------------- helpers
TOC_LINE = re.compile(r"\.{5,}|…{2,}|_{5,}")
SERIAL = re.compile(r"^\s*(?:\(?(?:\d{1,3}|[ivxIVX]{1,4})[.)]?|[a-zA-Z][.)]|[-•●▪*])\s*$")
LETTERS = re.compile(r"[A-Za-z]{3,}")
WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return WS.sub(" ", s).strip()


def to_int(s: str) -> int | None:
    try:
        return int(s.replace(",", ""))
    except ValueError:
        return None


def is_toc(line: str) -> bool:
    return bool(TOC_LINE.search(line))


# ----------------------------------------------------------------- departments
DEPARTMENTS: list[tuple[str, re.Pattern]] = [(name, re.compile(rx, re.I)) for name, rx in [
    ("Disaster Management", r"disaster management|\bDDMA\b|\bSEOC\b|\bDEOC\b|\bEOC\b|control room"),
    ("Fire & Rescue", r"\bfire\b|rescue services"),
    ("Police", r"\bpolice\b|superintendent of police|\bS\.?P\.?\b|inspector|\bSI\b"),
    ("Health", r"\bhealth\b|medical|hospital|\bPHC\b|doctor|ambulance"),
    ("Electricity", r"electricity|TANGEDCO|TNPDCL|\bTNEB\b|\bEB\b|electrical"),
    ("Civil Supplies", r"civil supplies|\bTNCSC\b|ration|\bPDS\b|fair price"),
    ("Public Works / Water Resources", r"\bPWD\b|public works|water resources|\bWRD\b|irrigation"),
    ("Highways", r"highways|\bNHAI\b"),
    ("Water Supply", r"metro ?water|CMWSSB|water supply|\bTWAD\b"),
    ("Municipal / Corporation", r"corporation|municipal|municipality|zonal officer|\bGCC\b"),
    ("Rural Development", r"rural development|panchayat|\bBDO\b|block development"),
    ("Revenue", r"revenue|collector|tahsildar|\btaluk\b|\bRDO\b|\bVAO\b|village administrative"),
    ("Fisheries", r"fisheries|fishermen"),
    ("Agriculture", r"agricultur|horticultur"),
    ("Transport", r"transport|\bMTC\b|\bRTO\b"),
    ("Education", r"education|\bCEO\b"),
    ("Telecom", r"\bBSNL\b|telecom"),
]]


def department_of(text: str) -> str | None:
    for name, rx in DEPARTMENTS:
        if rx.search(text):
            return name
    return None


ROLE = re.compile(
    r"(District Collector|Additional Collector|Sub[- ]Collector|Collector|Revenue Divisional Officer|\bRDO\b|"
    r"Deputy Tahsildar|Tahsildar|Village Administrative Officer|\bVAO\b|Block Development Officer|\bBDO\b|"
    r"Deputy Commissioner|Commissioner|Zonal Officer|Chief Engineer|Superintending Engineer|Executive Engineer|"
    r"Assistant Executive Engineer|Assistant Engineer|Superintendent of Police|Deputy Superintendent|"
    r"Sub[- ]Inspector|Inspector|Station Fire Officer|District Fire Officer|Fire Officer|Medical Officer|"
    r"Health Officer|Joint Director|Deputy Director|Director|Secretary|Chairperson|Nodal Officer|"
    r"Officer[- ]in[- ]charge|Control Room|Helpline)", re.I)


# ----------------------------------------------------------------- contacts
MOBILE = re.compile(r"(?<![\d])(?:\+91[\s-]?|0)?([6-9]\d{4})[\s-]?(\d{5})(?![\d])")
STD = re.compile(r"(?<![\d])(0\d{2,4})[\s-](\d{6,8})(?![\d])")
LOCAL = re.compile(r"(?<![\d-])(\d{7,8})(?![\d])")
PHONE_CUE = re.compile(r"(?:^|\b)(o:|off\.?|office|ph\.?|phone|tel\.?|telephone|fax|res\.?|landline|mob\.?|mobile|cell)(?=\W|$)", re.I)
HELPLINE = re.compile(r"(?<![\d])(1070|1077|108|112|101|100|104|1912|1913|1800[\s-]?\d{3}[\s-]?\d{4})(?![\d])")
HELPLINE_CUE = re.compile(r"toll[\s-]?free|helpline|help line|\bdial\b|control room|emergency", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MALFORMED = re.compile(r"(?<![\d])[6-9]\d{8}(?![\d])|(?<![\d])[6-9]\d{10}(?![\d])")


def phones_in(lines: list[str], i: int) -> list[str]:
    line = lines[i]
    found: list[str] = []
    for m in MOBILE.finditer(line):
        found.append(m.group(1) + m.group(2))
    masked = MOBILE.sub(" ", line)
    for m in STD.finditer(masked):
        found.append(f"{m.group(1)}-{m.group(2)}")
    masked = STD.sub(" ", masked)
    near_mobile = any(MOBILE.search(lines[j]) for j in range(max(0, i - 2), min(len(lines), i + 3)) if j != i)
    for m in LOCAL.finditer(masked):
        only_number = not LETTERS.search(masked.replace(m.group(1), ""))
        if PHONE_CUE.search(line) or (only_number and near_mobile):
            found.append(m.group(1))
    if HELPLINE_CUE.search(line):
        for m in HELPLINE.finditer(masked):
            found.append(m.group(1).replace(" ", ""))
    out: list[str] = []
    for p in found:
        if p not in out:
            out.append(p)
    return out


def _rest(line: str) -> str:
    s = EMAIL.sub(" ", line)
    s = MOBILE.sub(" ", s)
    s = STD.sub(" ", s)
    s = re.sub(r"\d{3,}", " ", s)
    s = PHONE_CUE.sub(" ", s)
    s = re.sub(r"[|/:–—-]+", " ", s)
    return norm(s).strip(" ,.;")


def extract_contacts(page: int, lines: list[str]) -> list[Record]:
    recs: list[Record] = []
    label_buf: list[tuple[int, str]] = []
    cur: dict | None = None

    def close() -> None:
        if cur is None:
            return
        label = " · ".join(cur["label"])
        if not label:
            return
        quote = "\n".join(lines[cur["start"]:cur["end"] + 1])
        recs.append(Record("contact", label[:300], page, quote, {
            "phones": cur["phones"], "emails": cur["emails"],
            "role": (ROLE.search(label).group(1) if ROLE.search(label) else None),
            "department": department_of(label),
        }))

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or is_toc(line):
            continue
        phones = phones_in(lines, i)
        emails = EMAIL.findall(line)
        if phones or emails:
            rest = _rest(line)
            if cur is not None and i - cur["end"] <= 1 and not LETTERS.search(rest):
                cur["phones"] += [p for p in phones if p not in cur["phones"]]
                cur["emails"] += [e for e in emails if e not in cur["emails"]]
                cur["end"] = i
            else:
                close()
                buf = label_buf[-4:]
                label = [t for _, t in buf] + ([rest] if LETTERS.search(rest) else [])
                cur = {"label": label, "phones": phones, "emails": emails,
                       "start": buf[0][0] if buf else i, "end": i}
            label_buf = []
        elif SERIAL.match(line):
            label_buf = []
        elif LETTERS.search(line) and len(line) <= 160:
            label_buf.append((i, line))
            label_buf = label_buf[-6:]
    close()
    return recs


# ----------------------------------------------------------------- shelters
SHELTER_WORDS = re.compile(r"relief cent(?:re|er)s?|relief camps?|cyclone shelters?|flood shelters?|"
                           r"multi[- ]purpose (?:shelter|hall)s?|temporary shelters?|evacuation cent(?:re|er)s?", re.I)
SHELTER_PLACE = re.compile(r"\b(school|college|hall|mandapam|mahal|marriage|community|shelter|church|mosque|"
                           r"temple|auditorium|stadium|hostel|kalyana)\b", re.I)
AGGREGATE = re.compile(r"(\d[\d,]*)\s+(relief cent(?:re|er)s?|relief camps?|(?:cyclone |flood |multi[- ]purpose )?"
                       r"shelters?)\b[^.\n]{0,60}?capacity\s+(?:of\s+)?(?:about\s+)?(\d[\d,]*)", re.I)
GENERIC_SHELTER_WORDS = {
    "govt", "government", "high", "higher", "secondary", "primary", "middle", "elementary", "school", "schools",
    "community", "hall", "halls", "cyclone", "flood", "shelter", "shelters", "nearest", "safer", "safe", "relief",
    "centre", "center", "centres", "college", "marriage", "kalyana", "mandapam", "mahal", "corporation", "panchayat",
    "union", "the", "and", "aided", "model", "municipal", "girls", "boys", "multi", "purpose", "building", "camp",
}
NUM_CELL = re.compile(r"^\s*(\d{1,6})\s*$")
NUMERIC_HEADERS = [("serial", re.compile(r"^(s\.?\s*l\.?\s*no|s\.?\s*no|sl\.?|serial)", re.I)),
                   ("ward", re.compile(r"^ward", re.I)), ("zone", re.compile(r"^zone", re.I)),
                   ("division", re.compile(r"^division", re.I)), ("capacity", re.compile(r"^capacity", re.I))]


def _shelter_table_rows(page: int, lines: list[str]) -> list[Record]:
    out: list[Record] = []
    for h, line in enumerate(lines):
        if not re.match(r"^\s*capacity\b", line, re.I) or len(line) > 40:
            continue
        lo = max(0, h - 8)
        name_idx = next((j for j in range(h, min(len(lines), h + 10)) if re.search(r"\bname\b", lines[j], re.I)), None)
        if name_idx is None:
            continue
        cols: list[str] = []
        for j in range(lo, name_idx):
            for key, rx in NUMERIC_HEADERS:
                if rx.match(lines[j].strip()) and key not in cols:
                    cols.append(key)
        if "capacity" not in cols:
            continue
        cap_i = cols.index("capacity")
        j = name_idx + 1
        while j < len(lines) and not NUM_CELL.match(lines[j]):
            j += 1
        while j < len(lines):
            start = j
            nums = []
            while j < len(lines) and NUM_CELL.match(lines[j]):
                nums.append(int(NUM_CELL.match(lines[j]).group(1)))
                j += 1
            texts: list[tuple[int, str]] = []
            while j < len(lines) and not NUM_CELL.match(lines[j]):
                if lines[j].strip():
                    texts.append((j, lines[j].strip()))
                j += 1
            if len(nums) != len(cols) or not texts:  # only rows that align with the header's numeric columns
                continue
            name_lines = []
            end = start
            for k, (idx, t) in enumerate(texts[:6]):
                name_lines.append(t)
                end = idx
                if SHELTER_PLACE.search(t):
                    if k + 1 < len(texts) and re.match(r"^(mandapam|mahal|hall|school|college|centre|center)\b",
                                                        texts[k + 1][1], re.I):
                        name_lines.append(texts[k + 1][1])
                        end = texts[k + 1][0]
                    break
            else:
                continue
            capacity = nums[cap_i]
            if not 10 <= capacity <= 100000:
                continue
            fields = {"capacity": capacity}
            if "ward" in cols and cols.index("ward") < len(nums):
                fields["ward"] = nums[cols.index("ward")]
            out.append(Record("shelter", norm(" ".join(name_lines)).strip(" ,"), page,
                              "\n".join(lines[start:end + 1]), fields))
    return out


def extract_shelters(page: int, lines: list[str]) -> list[Record]:
    text = "\n".join(lines)
    recs: list[Record] = []
    for raw in lines:
        if is_toc(raw):
            continue
        for m in AGGREGATE.finditer(raw):
            n, cap = to_int(m.group(1)), to_int(m.group(3))
            if n and cap and 1 <= n <= 5000 and 10 <= cap <= 5_000_000:
                recs.append(Record("shelter", f"{n} {m.group(2).lower()} (total)", page, raw.strip(),
                                   {"count": n, "capacity": cap, "aggregate": True}))
    if re.search(r"capacity", text, re.I) and (SHELTER_WORDS.search(text) or re.search(r"relief", text, re.I)):
        recs += _shelter_table_rows(page, lines)
    if len(SHELTER_WORDS.findall(text)) >= 2:
        seen = {norm(r.label).lower() for r in recs}
        for raw in lines:
            t = raw.strip()
            if (8 <= len(t) <= 70 and t[:1].isupper() and SHELTER_PLACE.search(t) and not is_toc(t)
                    and re.search(r"\b(govt|government|school|hall|mandapam|shelter|college|mahal)\b", t, re.I)
                    and not re.search(r"\b(shall|will|should|is|are|was|were|department|education|list of)\b", t, re.I)):
                key = norm(t).lower()
                specific = [w for w in re.findall(r"[a-z]{3,}", key) if w not in GENERIC_SHELTER_WORDS]
                place_first = SHELTER_PLACE.match(t) is not None and len(t.split()) < 3
                if specific and not place_first and key not in seen:
                    seen.add(key)
                    recs.append(Record("shelter", norm(t).strip(" ,"), page, t, {"capacity": None}))
    return recs


# ----------------------------------------------------------------- resources
RESOURCE_ITEMS: dict[str, str] = {
    "Boats": r"(?:country |fibre |rubber |inflatable )?boats?|OBMs?|out ?board motors?",
    "Life jackets": r"life ?jackets?",
    "Life buoys": r"life ?buoys?",
    "Earth movers / JCB": r"JCB[’']?s?|earth ?movers?|poclains?|hitachis?|excavators?|backhoe loaders?",
    "Generators": r"generators?|gen ?sets?|DG sets?",
    "Tree cutters / power saws": r"power saws?|chain saws?|tree cutt\w+(?: machines?)?|wood cutt\w+(?: machines?)?",
    "Pumps": r"(?:motor |dewatering |diesel |submersible )?pumps?(?: sets?)?",
    "Ambulances": r"ambulances?",
    "Lorries / vehicles": r"lorr(?:y|ies)|tippers?|trucks?|tractors?|vehicles?|jeeps?",
    "Cranes": r"cranes?",
    "Sand bags": r"sand ?bags?|gunny bags?",
    "Tarpaulins": r"tarpaulins?",
    "Water tankers": r"water tankers?",
}
_ITEM_RX = {k: re.compile(r"\b(" + v + r")\b", re.I) for k, v in RESOURCE_ITEMS.items()}
QTY = re.compile(r"(?<![\d.,/:-])(\d{1,5})(?![\d.,:/-])\s*(nos?\.?|numbers?|units?)?", re.I)
PAST = re.compile(r"\b(were|was|had been|used|deployed|put in service|utili[sz]ed|pressed into)\b", re.I)


def _numbered_index(lines: list[str], i: int, page: int) -> bool:
    """[serial] [item] [number] [serial+1] — a list of annexures/maps with page numbers."""
    prev = NUM_CELL.match(lines[i - 1]) if i >= 1 else None
    nxt = NUM_CELL.match(lines[i + 1]) if i + 1 < len(lines) else None
    after = NUM_CELL.match(lines[i + 2]) if i + 2 < len(lines) else None
    if prev and after and int(after.group(1)) == int(prev.group(1)) + 1:
        return True
    return bool(prev and nxt and abs(int(nxt.group(1)) - page) <= 60)


def extract_resources(page: int, lines: list[str]) -> list[Record]:
    recs: list[Record] = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or is_toc(line) or re.search(r"\d{7,}", line):
            continue
        for item, rx in _ITEM_RX.items():
            m = rx.search(line)
            if not m:
                continue
            if item == "Ambulances" and re.search(r"\b108\s*ambulance", line, re.I):
                continue
            qty, end = None, i
            best = None
            for q in QTY.finditer(line):
                n = int(q.group(1))
                if 1900 <= n <= 2100 and not q.group(2):
                    continue
                gap = min(abs(q.start() - m.end()), abs(m.start() - q.end()))
                if gap <= 14 and (best is None or gap < best[0]):
                    best = (gap, n)
            if best:
                qty = best[1]
            elif i + 1 < len(lines) and NUM_CELL.match(lines[i + 1]) and len(line) <= 60 \
                    and not _numbered_index(lines, i, page):
                qty, end = int(NUM_CELL.match(lines[i + 1]).group(1)), i + 1
            if qty is None or qty == 0 and "nil" not in line.lower():
                continue
            nxt = lines[end + 1].strip() if end + 1 < len(lines) else ""
            ctx = line + (" " + nxt if nxt[:1].islower() else "")  # follow a wrapped sentence only
            recs.append(Record("resource", item, page, "\n".join(lines[i:end + 1]), {
                "item": item, "quantity": qty, "context": "past_use" if PAST.search(ctx) else "inventory",
                "department": department_of(line + " " + (lines[i - 1] if i else "")),
            }))
    return recs


# ----------------------------------------------------------------- responsibilities & procedures
DUTY = re.compile(r"\b(shall|will|should|responsible for|to ensure|ensure|arrange|deploy|maintain|provide|"
                  r"coordinate|monitor|supervise)\b", re.I)
PROC = re.compile(r"\b(SOPs?|standard operating procedures?|action plans?|check ?lists?|evacuation plans?|"
                  r"early warning|emergency support functions?|ESF|incident response system|contingency plan|"
                  r"do'?s and don'?ts)\b", re.I)


DOC_TITLE = re.compile(r"disaster ma\w+\s+(?:perspective\s+|action\s+)?plan|\bDDMP\b|\bCDMP\b", re.I)


def extract_responsibilities(page: int, lines: list[str]) -> list[Record]:
    recs: list[Record] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if 30 <= len(line) <= 400 and not is_toc(line) and DUTY.search(line):
            dept = department_of(line)
            if dept:
                end = i
                while end + 1 < len(lines) and end - i < 2 and lines[end + 1][:1].islower():
                    end += 1
                recs.append(Record("responsibility", dept, page, "\n".join(lines[i:end + 1]), {"department": dept}))
                i = end
        i += 1
    return recs[:15]


def extract_procedures(page: int, lines: list[str]) -> list[Record]:
    recs: list[Record] = []
    for raw in lines:
        t = raw.strip()
        if not (6 <= len(t) <= 100) or is_toc(t) or not PROC.search(t) or DOC_TITLE.search(t):
            continue
        letters = [c for c in t if c.isalpha()]
        upper = sum(c.isupper() for c in letters) / max(1, len(letters))
        if upper >= 0.6 or re.match(r"^\d+(\.\d+)*\s", t) or t.endswith(":"):
            recs.append(Record("procedure", norm(t).rstrip(":"), page, t, {}))
    return recs


# ----------------------------------------------------------------- critical assets named in the plan
ASSET_TYPES: dict[str, str] = {
    "grain_storage": r"godowns?|warehouses?|\bFCI\b|food corporation|\bTNCSC\b|civil supplies corporation|granar(?:y|ies)",
    "ration_shop": r"fair price shops?|ration shops?|PDS outlets?",
    "cold_storage": r"cold storages?|cold chain|ice plants?|cold rooms?",
    "seed_store": r"seed (?:farms?|banks?|stores?|godowns?|processing units?|cent(?:re|er)s?)",
    "substation": r"sub[- ]?stations?",
    "water_works": r"water treatment plants?|pumping stations?|sewage treatment plants?|head ?works",
    "fuel": r"fuel (?:depots?|stations?|bunks?)|petrol (?:bunks?|pumps?)|oil depots?",
}
_ASSET_RX = {k: re.compile(r"\b(" + v + r")\b", re.I) for k, v in ASSET_TYPES.items()}


SENTENCE = re.compile(r"\b(is|are|was|were|has|have|had|each|these|this|which|there)\b", re.I)


def extract_assets(page: int, lines: list[str]) -> list[Record]:
    recs: list[Record] = []
    for raw in lines:
        t = raw.strip()
        if not (8 <= len(t) <= 140) or is_toc(t) or DUTY.search(t) or t[:1].islower() or SENTENCE.search(t):
            continue
        for typ, rx in _ASSET_RX.items():
            m = rx.search(t)
            if not m:
                continue
            others = [w for w in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", t) if not rx.fullmatch(w)]
            named = bool(re.search(r"\b(at|near|in)\s+[A-Z]", t)) or len(others) >= 2
            count = re.search(r"(\d{1,5})\s+(?:\w+\s){0,2}" + re.escape(m.group(1)), t)
            if not (named or count):
                continue
            fields = {"asset_type": typ}
            if count:
                fields["count"] = int(count.group(1))
            recs.append(Record("asset", norm(t), page, t, fields))
            break
    return recs


# ----------------------------------------------------------------- page driver
def extract_page(page: int, text: str) -> list[Record]:
    lines = text.split("\n")
    out: list[Record] = []
    for fn in (extract_contacts, extract_shelters, extract_resources, extract_responsibilities,
               extract_procedures, extract_assets):
        out += fn(page, lines)
    return out


def malformed_numbers(text: str) -> list[str]:
    """9- or 11-digit numbers on contact-like lines: probably a mistyped mobile number."""
    out = []
    for line in text.split("\n"):
        if PHONE_CUE.search(line) or ROLE.search(line) or len(line.strip()) <= 14:
            out += MALFORMED.findall(line)
    return out


def dedupe(records: list[Record]) -> list[Record]:
    seen: set[tuple] = set()
    out = []
    for r in records:
        if r.kind == "contact":
            key = ("contact", tuple(sorted(r.fields.get("phones") or [])) or norm(r.quote).lower())
        else:
            key = (r.kind, norm(r.label).lower(), r.fields.get("quantity"), r.fields.get("capacity"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def merge_chunks(texts: list[str], max_overlap: int = 400) -> str:
    """Rebuild a page from its ordered chunks, removing the chunker's overlap."""
    out = ""
    for t in texts:
        if not out:
            out = t
            continue
        k = 0
        for n in range(min(max_overlap, len(out), len(t)), 10, -1):
            if out.endswith(t[:n]):
                k = n
                break
        out = out + ("" if k else "\n") + t[k:]
    return out
