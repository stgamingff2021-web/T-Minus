"""NDMA SACHET official alerts (CAP over RSS) — spec §5.1, §6.5 step 2.

SACHET distributes CAP alerts from IMD, CWC, INCOIS and SDMAs. The client:

1. fetches the configured RSS/Atom feed (``SACHET_FEED_URL``; (verify) the exact
   state/national feed URL on https://sachet.ndma.gov.in/);
2. for each item, uses an inline ``<alert>`` if the feed embeds CAP, otherwise fetches
   the linked CAP XML (bounded, concurrency-limited);
3. parses CAP 1.x ``info`` blocks: event, severity, urgency, certainty, effective,
   expires, sender, headline, description, area (areaDesc, polygons);
4. maps each alert to an official colour (red / orange / yellow) and to the monitored
   districts it covers (areaDesc name matching + point-in-polygon on the district HQ).

The colour mapping (verify which fields SACHET populates): an explicit colour word in
the parameters/headline/description wins; otherwise CAP severity Extreme -> red,
Severe -> orange, Moderate -> yellow. Parsing is defensive — any field may be absent.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from app.config import DistrictCfg, get_settings
from app.sources.base import SourceError, fetch

CAP_ITEM_LIMIT = 60
COLOR_WORDS = ("red", "orange", "yellow")
SEVERITY_COLOR = {"extreme": "red", "severe": "orange", "moderate": "yellow"}
RAIN_WORDS = ("rain", "flood", "cyclone", "depression", "thunderstorm", "storm", "deluge", "inundation", "landslide")
HEAT_WORDS = ("heat", "hot", "temperature", "warm night")
RIVER_WORDS = ("flood", "river", "dam", "inundation", "water level")


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
    link: str | None = None
    language: str | None = None
    summary_only: bool = False
    fetch_id: str | None = None

    def hazards(self) -> set[str]:
        text = " ".join(filter(None, [self.event, self.headline, self.description])).lower()
        out = set()
        if any(w in text for w in RAIN_WORDS):
            out.add("rain")
        if any(w in text for w in RIVER_WORDS):
            out.add("river")
        if any(w in text for w in HEAT_WORDS):
            out.add("heat")
        return out

    def is_active(self, now: dt.datetime) -> bool:
        if self.expires and self.expires < now:
            return False
        return True

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
            "link": self.link,
            "language": self.language,
            "summary_only": self.summary_only,
            "hazards": sorted(self.hazards()),
            "fetch_id": self.fetch_id,
        }


def _local(tag: Any) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _children(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name]


def _text(el, name: str) -> str | None:
    for c in el:
        if _local(c.tag) == name and c.text:
            return c.text.strip()
    return None


def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    for fmt in (None, "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            if fmt is None:
                t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                t = dt.datetime.strptime(s, fmt)
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
    """Parse one CAP document (any CAP 1.x namespace). One OfficialAlert per info block."""
    root = etree.fromstring(xml.encode() if isinstance(xml, str) else xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    if _local(root.tag) != "alert":
        found = [e for e in root.iter() if _local(e.tag) == "alert"]
        if not found:
            raise ValueError("no CAP <alert> element")
        root = found[0]
    ident = _text(root, "identifier") or ""
    sender = _text(root, "sender")
    sent = parse_time(_text(root, "sent"))
    out = []
    infos = _children(root, "info") or [root]
    for info in infos:
        params = " ".join(
            f"{_text(p, 'valueName') or ''} {_text(p, 'value') or ''}" for p in _children(info, "parameter")
        )
        areas = _children(info, "area")
        area_desc = "; ".join(filter(None, (_text(a, "areaDesc") for a in areas))) or None
        polys = []
        for a in areas:
            for p in _children(a, "polygon"):
                if p.text:
                    poly = _parse_polygon(p.text)
                    if poly:
                        polys.append(poly)
        severity = _text(info, "severity")
        headline = _text(info, "headline")
        desc = _text(info, "description")
        out.append(OfficialAlert(
            identifier=ident,
            sender=sender,
            agency=_text(info, "senderName") or sender,
            event=_text(info, "event"),
            severity=severity,
            urgency=_text(info, "urgency"),
            certainty=_text(info, "certainty"),
            color=detect_color(params, headline, _text(info, "event"), desc, severity=severity),
            issued=sent,
            effective=parse_time(_text(info, "effective")) or sent,
            expires=parse_time(_text(info, "expires")),
            headline=headline,
            description=desc,
            area_desc=area_desc,
            polygons=polys,
            link=link,
            language=_text(info, "language"),
        ))
    return out


@dataclass
class FeedItem:
    title: str | None
    link: str | None
    description: str | None
    published: dt.datetime | None
    guid: str | None
    inline_cap: Any = None


def parse_feed(xml: str) -> list[FeedItem]:
    root = etree.fromstring(xml.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=True))
    if root is None:
        raise ValueError("feed is not XML")
    items: list[FeedItem] = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        link = _text(el, "link")
        if not link:
            for c in el:
                if _local(c.tag) == "link" and c.get("href"):
                    link = c.get("href")
                    break
        inline = next((c for c in el.iter() if _local(c.tag) == "alert"), None)
        items.append(FeedItem(
            title=_text(el, "title"),
            link=link,
            description=_text(el, "description") or _text(el, "summary") or _text(el, "content"),
            published=parse_time(_text(el, "pubDate") or _text(el, "published") or _text(el, "updated")),
            guid=_text(el, "guid") or _text(el, "id"),
            inline_cap=inline,
        ))
    if not items and _local(root.tag) not in ("rss", "feed", "RDF", "channel"):
        raise ValueError(f"unexpected feed root <{_local(root.tag)}>")
    return items


def _validate_feed(payload: Any) -> None:
    if not isinstance(payload, str) or "<" not in payload[:2000]:
        raise ValueError("feed body is not XML")
    parse_feed(payload)


async def fetch_alerts() -> tuple[list[OfficialAlert], str]:
    """Fetch the SACHET feed and its CAP documents. Raises SourceError if the feed fails."""
    s = get_settings()
    res = await fetch("sachet", s.sachet_feed_url, parse="text", validate=_validate_feed)
    items = parse_feed(res.payload)
    alerts: list[OfficialAlert] = []
    sem = asyncio.Semaphore(6)

    async def load(item: FeedItem) -> list[OfficialAlert]:
        if item.inline_cap is not None:
            try:
                parsed = parse_cap(etree.tostring(item.inline_cap), link=item.link)
                for a in parsed:
                    a.fetch_id = res.fetch_id
                return parsed
            except (ValueError, etree.XMLSyntaxError):
                pass
        if item.link and item.link.startswith("http"):
            async with sem:
                try:
                    cap = await fetch("sachet", item.link, parse="text", attempts=2,
                                      validate=lambda p: parse_cap(p) and None)
                    parsed = parse_cap(cap.payload, link=item.link)
                    for a in parsed:
                        a.fetch_id = cap.fetch_id
                    return parsed
                except (SourceError, ValueError, etree.XMLSyntaxError):
                    pass
        # Fall back to the RSS summary itself (clearly flagged).
        return [OfficialAlert(
            identifier=item.guid or item.link or (item.title or ""),
            sender=None, agency=None, event=item.title, severity=None, urgency=None, certainty=None,
            color=detect_color(item.title, item.description), issued=item.published, effective=item.published,
            expires=None, headline=item.title, description=item.description, area_desc=item.title,
            link=item.link, summary_only=True, fetch_id=res.fetch_id,
        )]

    results = await asyncio.gather(*(load(it) for it in items[:CAP_ITEM_LIMIT]))
    for r in results:
        alerts.extend(r)
    return alerts, res.fetch_id


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
    """Does an alert's area cover a district? Name/alias match in areaDesc or HQ in polygon."""
    lat, lon = d.hq
    if any(_point_in_poly(lat, lon, poly) for poly in alert.polygons):
        return True
    area = (alert.area_desc or "") + " " + (alert.headline or "")
    if not area.strip():
        return False
    area_l = area.lower()
    for name in d.all_names():
        if re.search(rf"(?<![a-z]){re.escape(name.lower())}(?![a-z])", area_l):
            return True
    return False


def district_alerts(alerts: list[OfficialAlert], d: DistrictCfg, state_name: str, hazard: str,
                    now: dt.datetime) -> list[OfficialAlert]:
    return [a for a in alerts if a.is_active(now) and hazard in a.hazards() and alert_covers_district(a, d, state_name)]


def strongest_color(alerts: list[OfficialAlert]) -> str | None:
    order = {"red": 3, "orange": 2, "yellow": 1}
    best = None
    for a in alerts:
        if a.color in order and (best is None or order[a.color] > order[best]):
            best = a.color
    return best
