"""NDMA SACHET official alerts (CAP over RSS) — spec §5.1, §6.5 step 2.

Verified against the live service (2026-10-31):

* Per-state RSS 2.0 feeds exist at ``https://sachet.ndma.gov.in/cap_public_website/rss/<name>.xml``
  (Tamil Nadu = ``rss_tamil``, Puducherry = ``rss_puducherry``, Kerala = ``rss_kerala``,
  Andhra Pradesh = ``rss_andhra``, Odisha = ``rss_odisha``, …; see config/districts.yaml).
  The national feed ``rss_india.xml`` keeps only the latest 100 items of all states, so
  state feeds are used by default.
* Each item links to its CAP 1.2 document (``FetchXMLFile?identifier=…``) with ``cap:``
  prefixed elements, an English info block plus a regional-language block.
* Polygons are NOT inline: a "Polygon URL" parameter points to a separate file that is
  behind a web-application firewall (HTTP 403), so district matching uses the
  ``areaDesc``/headline names (e.g. "Tenkasi, Virudhunagar districts of Tamil Nadu") and
  the "LGD District Code" parameters are kept for reference.
* No colour code is published; IMD alerts carry CAP ``severity`` (Moderate / Severe /
  Extreme). Most items are 1–3 hour nowcasts ("Thunderstorm with Lightning", "Light
  Rain") that also use Severe/Moderate.

Floor policy (§6.5): only alerts about heavy rainfall, floods or cyclones act as the
official floor (severity Severe → orange → PREPARE, Extreme → red → ACT; an explicit
colour word wins). Thunderstorm/lightning and light/moderate-rain nowcasts are shown to
the officer as information but never raise a flood level on their own.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from app.config import DistrictCfg, get_config, get_settings
from app.sources.base import SourceError, fetch

CAP_FETCH_LIMIT = 80
ITEM_MAX_AGE_DAYS = 7
COLOR_WORDS = ("red", "orange", "yellow")
SEVERITY_COLOR = {"extreme": "red", "severe": "orange", "moderate": "yellow"}
HEAVY_RAIN_RE = re.compile(
    r"heavy\s*rain|heavy\s*rainfall|very\s*heavy|extremely\s*heavy|\bflood|flash\s*flood|cyclon|\bdeep\s*depression|"
    r"\bdepression\b|inundat|cloud\s*burst|cloudburst|deluge|storm\s*surge", re.I)
RIVER_RE = re.compile(r"\bflood|\briver\b|\bdam\b|water\s*level|inundat", re.I)
HEAT_RE = re.compile(r"heat\s*wave|heatwave|\bheat\b|hot\s*(and\s*humid|day|night)|warm\s*night", re.I)
RAIN_INFO_RE = re.compile(r"\brain|thunder|lightning|storm|shower", re.I)

_cap_cache: dict[str, list] = {}


@dataclass
class OfficialAlert:
    identifier: str
    sender: str | None
    agency: str | None
    event: str | None
    severity: str | None
    urgency: str | None
    certainty: str | None
    color: str | None
    issued: dt.datetime | None
    effective: dt.datetime | None
    expires: dt.datetime | None
    headline: str | None
    description: str | None
    area_desc: str | None
    polygons: list[list[tuple[float, float]]] = field(default_factory=list)
    lgd_codes: list[str] = field(default_factory=list)
    link: str | None = None
    language: str | None = None
    summary_only: bool = False
    fetch_id: str | None = None
    feed: str | None = None

    def _text(self) -> str:
        return " ".join(filter(None, [self.event, self.headline, self.description]))

    def hazards(self) -> set[str]:
        """Hazards for which this alert may act as the official FLOOR."""
        text = self._text()
        out = set()
        if HEAVY_RAIN_RE.search(text):
            out.add("rain")
        is_cwc = "CWC" in (self.agency or "").upper() or "CWC" in (self.sender or "").upper()
        if (RIVER_RE.search(text) and is_cwc) or re.search(r"\briver\b", text, re.I):
            out.add("river")
        if HEAT_RE.search(text):
            out.add("heat")
        return out

    def info_hazards(self) -> set[str]:
        """Hazards this alert is relevant to as INFORMATION (includes nowcasts)."""
        out = set(self.hazards())
        if RAIN_INFO_RE.search(self._text()):
            out.add("rain")
        return out

    def is_active(self, now: dt.datetime) -> bool:
        return not (self.expires and self.expires < now)

    def as_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "agency": self.agency or self.sender,
            "event": self.event,
            "severity": self.severity,
            "urgency": self.urgency,
            "certainty": self.certainty,
            "color": self.color,
            "issued": self.issued.isoformat() if self.issued else None,
            "effective": self.effective.isoformat() if self.effective else None,
            "expires": self.expires.isoformat() if self.expires else None,
            "headline": self.headline,
            "text": (self.description or "")[:1200],
            "area": self.area_desc,
            "lgd_codes": self.lgd_codes,
            "link": self.link,
            "language": self.language,
            "summary_only": self.summary_only,
            "floor_hazards": sorted(self.hazards()),
            "hazards": sorted(self.info_hazards()),
            "fetch_id": self.fetch_id,
            "feed": self.feed,
        }


def _local(tag: Any) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _children(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name]


def _text(el, name: str) -> str | None:
    for c in el:
        if _local(c.tag) == name and c.text and c.text.strip():
            return c.text.strip()
    return None


def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    for fmt in (None, "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            t = dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if fmt is None else dt.datetime.strptime(s, fmt)
            return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def _parse_polygon(text: str) -> list[tuple[float, float]]:
    pts = []
    for pair in text.split():
        try:
            lat, lon = pair.split(",")[:2]
            pts.append((float(lat), float(lon)))
        except ValueError:
            continue
    return pts if len(pts) >= 4 else []


def detect_color(*texts: str | None, severity: str | None = None) -> str | None:
    blob = " ".join(t for t in texts if t).lower()
    for c in COLOR_WORDS:  # most severe first
        if re.search(rf"\b{c}\b", blob):
            return c
    return SEVERITY_COLOR.get((severity or "").lower())


def parse_cap(xml: bytes | str, link: str | None = None) -> list[OfficialAlert]:
    """Parse one CAP document (any CAP 1.x namespace/prefix) into ONE alert.

    The English info block is preferred (SACHET adds a regional-language copy).
    """
    root = etree.fromstring(xml.encode() if isinstance(xml, str) else xml,
                            parser=etree.XMLParser(resolve_entities=False, no_network=True))
    if _local(root.tag) != "alert":
        found = [e for e in root.iter() if _local(e.tag) == "alert"]
        if not found:
            raise ValueError("no CAP <alert> element")
        root = found[0]
    infos = _children(root, "info")
    if not infos:
        raise ValueError("CAP alert has no <info>")
    info = next((i for i in infos if (_text(i, "language") or "").lower().startswith("en")), infos[0])
    params = [(_text(p, "valueName") or "", _text(p, "value") or "") for p in _children(info, "parameter")]
    areas = _children(info, "area")
    polys = [poly for a in areas for p in _children(a, "polygon") if p.text for poly in [_parse_polygon(p.text)] if poly]
    severity = _text(info, "severity")
    headline = _text(info, "headline")
    desc = _text(info, "description")
    sent = parse_time(_text(root, "sent"))
    param_text = " ".join(f"{k} {v}" for k, v in params if "url" not in k.lower())
    return [OfficialAlert(
        identifier=_text(root, "identifier") or "",
        sender=_text(root, "sender"),
        agency=_text(info, "senderName") or _text(root, "sender"),
        event=_text(info, "event"),
        severity=severity,
        urgency=_text(info, "urgency"),
        certainty=_text(info, "certainty"),
        color=detect_color(param_text, headline, _text(info, "event"), desc, severity=severity),
        issued=sent,
        effective=parse_time(_text(info, "effective")) or sent,
        expires=parse_time(_text(info, "expires")),
        headline=headline,
        description=desc,
        area_desc="; ".join(filter(None, (_text(a, "areaDesc") for a in areas))) or None,
        polygons=polys,
        lgd_codes=[v for k, v in params if k.strip().lower() == "lgd district code"],
        link=link,
        language=_text(info, "language"),
    )]


@dataclass
class FeedItem:
    title: str | None
    link: str | None
    description: str | None
    published: dt.datetime | None
    guid: str | None
    author: str | None = None
    inline_cap: Any = None


def parse_feed(xml: str) -> list[FeedItem]:
    root = etree.fromstring(xml.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=True))
    if root is None:
        raise ValueError("feed is not XML")
    items: list[FeedItem] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        link = _text(el, "link")
        if not link:
            for c in el:
                if _local(c.tag) == "link" and c.get("href"):
                    link = c.get("href")
                    break
        author = _text(el, "author")
        m = re.search(r"\(([^)]+)\)", author or "")
        items.append(FeedItem(
            title=_text(el, "title"), link=link,
            description=_text(el, "description") or _text(el, "summary") or _text(el, "content"),
            published=parse_time(_text(el, "pubDate") or _text(el, "published") or _text(el, "updated")),
            guid=_text(el, "guid") or _text(el, "id"),
            author=m.group(1) if m else author,
            inline_cap=next((c for c in el.iter() if _local(c.tag) == "alert"), None),
        ))
    if not items and _local(root.tag) not in ("rss", "feed", "RDF", "channel"):
        raise ValueError(f"unexpected feed root <{_local(root.tag)}>")
    return items


def _validate_feed(payload: Any) -> None:
    if not isinstance(payload, str) or "<" not in payload[:2000]:
        raise ValueError("feed body is not XML")
    parse_feed(payload)


def feed_urls() -> list[tuple[str, str]]:
    """(label, url) for every monitored state's SACHET feed (+ national if enabled)."""
    s = get_settings()
    cfg = get_config()
    out = []
    for code in sorted({d.state for d in cfg.districts}):
        name = cfg.sachet_feeds.get(code)
        if name:
            out.append((code, f"{s.sachet_state_feed_base.rstrip('/')}/{name}.xml"))
    if s.sachet_use_national_feed or not out:
        out.append(("IN", s.sachet_feed_url))
    return out


async def fetch_alerts() -> tuple[list[OfficialAlert], str]:
    """Fetch every configured SACHET feed and the CAP documents of recent items.

    Raises SourceError only if EVERY feed fails; partial failures are logged per feed.
    """
    feeds = feed_urls()
    results = await asyncio.gather(*(fetch("sachet", url, parse="text", validate=_validate_feed) for _, url in feeds),
                                   return_exceptions=True)
    items: list[tuple[str, FeedItem, str]] = []
    fetch_ids, errors = [], []
    for (label, url), res in zip(feeds, results):
        if isinstance(res, SourceError):
            errors.append(f"{label}: {res.message}")
            continue
        if isinstance(res, Exception):
            errors.append(f"{label}: {res}")
            continue
        fetch_ids.append(res.fetch_id)
        for it in parse_feed(res.payload):
            items.append((label, it, res.fetch_id))
    if not fetch_ids:
        raise SourceError("sachet", "; ".join(errors) or "all SACHET feeds failed")
    now = dt.datetime.now(dt.timezone.utc)
    seen: set[str] = set()
    recent = []
    for label, it, fid in items:
        key = it.guid or it.link or it.title or ""
        if key in seen:
            continue
        seen.add(key)
        if it.published and (now - it.published).days > ITEM_MAX_AGE_DAYS:
            continue
        recent.append((label, it, fid))
    sem = asyncio.Semaphore(6)

    async def load(label: str, item: FeedItem, feed_fid: str) -> list[OfficialAlert]:
        cache_key = item.guid or item.link or ""
        if cache_key and cache_key in _cap_cache:
            return _cap_cache[cache_key]
        parsed: list[OfficialAlert] | None = None
        if item.inline_cap is not None:
            try:
                parsed = parse_cap(etree.tostring(item.inline_cap), link=item.link)
                for a in parsed:
                    a.fetch_id = feed_fid
            except (ValueError, etree.XMLSyntaxError):
                parsed = None
        if parsed is None and item.link and item.link.startswith("http"):
            async with sem:
                try:
                    cap = await fetch("sachet", item.link, parse="text", attempts=2,
                                      validate=lambda p: parse_cap(p) and None)
                    parsed = parse_cap(cap.payload, link=item.link)
                    for a in parsed:
                        a.fetch_id = cap.fetch_id
                except (SourceError, ValueError, etree.XMLSyntaxError):
                    parsed = None
        if parsed is None:  # fall back to the RSS summary itself (clearly flagged)
            parsed = [OfficialAlert(
                identifier=item.guid or item.link or (item.title or ""), sender=None, agency=item.author, event=item.title,
                severity=None, urgency=None, certainty=None, color=detect_color(item.title, item.description),
                issued=item.published, effective=item.published, expires=None, headline=item.title,
                description=item.description, area_desc=item.title, link=item.link, summary_only=True, fetch_id=feed_fid)]
        else:
            if cache_key:
                _cap_cache[cache_key] = parsed
        for a in parsed:
            a.feed = label
            if not a.agency or a.agency == a.sender:
                a.agency = item.author or a.agency
        return parsed

    loaded = await asyncio.gather(*(load(lbl, it, fid) for lbl, it, fid in recent[:CAP_FETCH_LIMIT]))
    alerts = [a for group in loaded for a in group]
    return alerts, fetch_ids[0]


def _point_in_poly(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        y1, x1 = poly[i]
        y2, x2 = poly[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_int = x1 + (lat - y1) * (x2 - x1) / ((y2 - y1) or 1e-12)
            if lon < x_int:
                inside = not inside
    return inside


def alert_covers_district(alert: OfficialAlert, d: DistrictCfg, state_name: str) -> bool:
    """Name/alias match in areaDesc or headline (word-bounded), or HQ inside a CAP polygon."""
    lat, lon = d.hq
    if any(_point_in_poly(lat, lon, poly) for poly in alert.polygons):
        return True
    area = " ".join(filter(None, [alert.area_desc, alert.headline]))
    if not area.strip():
        return False
    area_l = area.lower()
    for name in d.all_names():
        if re.search(rf"(?<![a-z]){re.escape(name.lower())}(?![a-z])", area_l):
            return True
    return False


def district_alerts(alerts: list[OfficialAlert], d: DistrictCfg, state_name: str, hazard: str,
                    now: dt.datetime) -> list[OfficialAlert]:
    """Active alerts that may act as the official floor for this district and hazard."""
    return [a for a in alerts if a.is_active(now) and hazard in a.hazards() and alert_covers_district(a, d, state_name)]


def district_info_alerts(alerts: list[OfficialAlert], d: DistrictCfg, state_name: str, hazard: str,
                         now: dt.datetime) -> list[OfficialAlert]:
    """Active alerts shown as information only (e.g. thunderstorm nowcasts) — never a floor."""
    return [a for a in alerts if a.is_active(now) and hazard in a.info_hazards() and hazard not in a.hazards()
            and alert_covers_district(a, d, state_name)]


def strongest_color(alerts: list[OfficialAlert]) -> str | None:
    order = {"red": 3, "orange": 2, "yellow": 1}
    best = None
    for a in alerts:
        if a.color in order and (best is None or order[a.color] > order[best]):
            best = a.color
    return best
