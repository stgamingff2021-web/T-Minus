"""CAP 1.2 builder + validator (spec §6.9, AC-09).

Built by code from approved content and validated against the official OASIS CAP 1.2
XSD (``xsd/CAP-v1.2.xsd``). In this prototype ``status`` is ALWAYS ``Exercise``.

Mapping (§6.9):
  msgType   Alert (first trigger) | Update (level change) | Cancel (officer-confirmed stand-down)
  scope     Public                category  Met
  urgency   Future (> ~1 h away) | Expected (within the hour) | Immediate (ongoing)
  severity  heavy -> Moderate | very heavy -> Severe | extremely heavy -> Extreme
  certainty P > 50% -> Likely | P <= 50% -> Possible  (CAP's own definitions)
  onset / expires   earliest plausible onset / end of the event window
  English info block + Tamil info block (language "ta-IN"); area = district name +
  simplified district boundary polygon (or a circle around the HQ if no boundary).
"""
from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"
XSD_PATH = Path(__file__).parent / "xsd" / "CAP-v1.2.xsd"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SEVERITY = {"heavy": "Moderate", "very_heavy": "Severe", "extremely_heavy": "Extreme"}
EVENT_NAME = {"heavy": "Heavy rainfall", "very_heavy": "Very heavy rainfall", "extremely_heavy": "Extremely heavy rainfall"}
RESPONSE = {"WATCH": "Monitor", "PREPARE": "Prepare", "ACT": "Prepare", "QUIET": "Monitor"}


def cap_time(t: dt.datetime) -> str:
    """CAP dateTime: YYYY-MM-DDThh:mm:ss+hh:mm (no 'Z' allowed by the XSD pattern)."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(IST).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def urgency(t_minus_h: float | None) -> str:
    if t_minus_h is None:
        return "Unknown"
    if t_minus_h <= 0:
        return "Immediate"
    if t_minus_h <= 1:
        return "Expected"
    return "Future"


def certainty(p: float | None) -> str:
    if p is None:
        return "Unknown"
    return "Likely" if p > 0.5 else "Possible"


def severity(category: str | None) -> str:
    return SEVERITY.get(category or "", "Unknown")


def safe_id(s: str) -> str:
    """CAP identifiers must not contain spaces, commas, '<' or '&'."""
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in s)


@dataclass
class InfoBlock:
    language: str
    event: str
    headline: str
    description: str
    instruction: str
    urgency: str
    severity: str
    certainty: str
    response_type: str | None = None
    onset: dt.datetime | None = None
    expires: dt.datetime | None = None
    effective: dt.datetime | None = None
    sender_name: str | None = None
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass
class Area:
    desc: str
    polygon: str | None = None
    circle: str | None = None


def build_cap(
    *,
    identifier: str,
    sender: str,
    sent: dt.datetime,
    msg_type: str,
    infos: list[InfoBlock],
    area: Area,
    references: list[str] | None = None,
    note: str | None = None,
) -> str:
    if msg_type not in ("Alert", "Update", "Cancel"):
        raise ValueError("msgType must be Alert, Update or Cancel")
    E = lambda tag, text=None, parent=None: _el(parent, tag, text)  # noqa: E731
    root = etree.Element(f"{{{CAP_NS}}}alert", nsmap={None: CAP_NS})
    E("identifier", safe_id(identifier), root)
    E("sender", safe_id(sender), root)
    E("sent", cap_time(sent), root)
    E("status", "Exercise", root)  # prototype: never "Actual"
    E("msgType", msg_type, root)
    E("scope", "Public", root)
    if note:
        E("note", note, root)
    if references:
        E("references", " ".join(references), root)
    for info in infos:
        i = E("info", None, root)
        E("language", info.language, i)
        E("category", "Met", i)
        E("event", info.event, i)
        if info.response_type:
            E("responseType", info.response_type, i)
        E("urgency", info.urgency, i)
        E("severity", info.severity, i)
        E("certainty", info.certainty, i)
        if info.effective:
            E("effective", cap_time(info.effective), i)
        if info.onset:
            E("onset", cap_time(info.onset), i)
        if info.expires:
            E("expires", cap_time(info.expires), i)
        if info.sender_name:
            E("senderName", info.sender_name, i)
        E("headline", info.headline, i)
        E("description", info.description, i)
        E("instruction", info.instruction, i)
        for k, v in info.parameters.items():
            p = E("parameter", None, i)
            E("valueName", k, p)
            E("value", v, p)
        a = E("area", None, i)
        E("areaDesc", area.desc, a)
        if area.polygon:
            E("polygon", area.polygon, a)
        if area.circle:
            E("circle", area.circle, a)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode("utf-8")


def _el(parent, tag: str, text: str | None):
    el = etree.SubElement(parent, f"{{{CAP_NS}}}{tag}") if parent is not None else etree.Element(f"{{{CAP_NS}}}{tag}")
    if text is not None:
        el.text = text
    return el


@functools.lru_cache
def _schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(XSD_PATH)))


def validate_cap(xml: str) -> dict:
    """Validate against the official OASIS CAP 1.2 XSD. Returns {valid, errors, schema}."""
    try:
        doc = etree.fromstring(xml.encode("utf-8"), parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as exc:
        return {"valid": False, "errors": [f"XML syntax error: {exc}"], "schema": XSD_PATH.name}
    schema = _schema()
    ok = schema.validate(doc)
    errors = [f"line {e.line}: {e.message}" for e in schema.error_log] if not ok else []
    status = doc.findtext(f"{{{CAP_NS}}}status")
    if status != "Exercise":
        ok = False
        errors.append(f"status must be 'Exercise' in this prototype (got {status!r})")
    return {"valid": bool(ok), "errors": errors, "schema": "OASIS CAP 1.2 (CAP-v1.2.xsd)"}
