"""Download district boundaries from a published source and record provenance (spec §5.3).

Sources, in order:
  1. OpenStreetMap via Overpass (ODbL) — admin_level=5 district relations inside each
     configured state (ISO 3166-2 code), assembled into polygons with shapely.
  2. geoBoundaries gbOpen IND ADM2 (fallback) — the API returns the download URL and
     the licence for the current release.

Writes data/boundaries/districts.geojson (properties.district_id matches
config/districts.yaml) and data/boundaries/PROVENANCE.json. Nothing is invented: a
district that cannot be matched is reported and simply has no polygon (the app then
falls back to its HQ point and says so).

Usage (from backend/):  python -m scripts.fetch_boundaries [--source osm|geoboundaries]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata

import httpx
from rapidfuzz import fuzz
from shapely.geometry import LineString, MultiPolygon, Polygon, mapping, shape
from shapely.ops import linemerge, polygonize, unary_union

from app.config import get_config, get_settings
from app.geo import PROVENANCE_FILE, boundaries_path

ISO = {"TN": "IN-TN", "PY": "IN-PY", "KL": "IN-KL", "AP": "IN-AP", "OR": "IN-O[RD]", "AS": "IN-AS", "MH": "IN-MH"}
GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen/IND/ADM2/"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(district|dist|the)\b", " ", s)
    return re.sub(r"[^a-z]+", "", s)


def match_district(names: list[str], districts) -> str | None:
    best, best_score = None, 0
    for d in districts:
        for cand in d.all_names():
            for n in names:
                if not n:
                    continue
                score = 100 if norm(n) == norm(cand) else fuzz.ratio(norm(n), norm(cand))
                if score > best_score:
                    best, best_score = d.id, score
    return best if best_score >= 88 else None


def assemble(rel: dict) -> Polygon | MultiPolygon | None:
    outer, inner = [], []
    for m in rel.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        line = LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
        (inner if m.get("role") == "inner" else outer).append(line)
    if not outer:
        return None
    polys = list(polygonize(linemerge(outer)))
    if not polys:
        return None
    geom = unary_union(polys)
    if inner:
        holes = list(polygonize(linemerge(inner)))
        if holes:
            geom = geom.difference(unary_union(holes))
    return geom if geom.is_valid else geom.buffer(0)


def from_osm(client: httpx.Client, url: str) -> tuple[list[dict], list[str]]:
    cfg = get_config()
    feats, notes = [], []
    for code in sorted({d.state for d in cfg.districts}):
        iso = ISO.get(code)
        if not iso:
            continue
        q = (f'[out:json][timeout:300];area["ISO3166-2"~"^{iso}$"]["admin_level"="4"]->.s;'
             f'relation(area.s)["boundary"="administrative"]["admin_level"="5"];out geom;')
        r = client.post(url, data={"data": q}, timeout=360)
        r.raise_for_status()
        rels = r.json().get("elements", [])
        districts = [d for d in cfg.districts if d.state == code]
        for rel in rels:
            tags = rel.get("tags", {})
            names = [tags.get(k) for k in ("name:en", "name", "official_name", "alt_name", "old_name")]
            did = match_district([n for n in names if n], districts)
            if not did:
                continue
            geom = assemble(rel)
            if geom is None or geom.is_empty:
                notes.append(f"{did}: could not assemble polygon for OSM relation {rel.get('id')}")
                continue
            feats.append({"type": "Feature", "geometry": mapping(geom.simplify(0.0008, preserve_topology=True)),
                          "properties": {"district_id": did, "name": tags.get("name:en") or tags.get("name"),
                                         "osm_relation": rel.get("id")}})
        notes.append(f"{code}: {len(rels)} OSM admin_level=5 relations")
    return feats, notes


def from_geoboundaries(client: httpx.Client) -> tuple[list[dict], list[str], dict]:
    meta = client.get(GEOBOUNDARIES_API, timeout=60).json()
    gj_url = meta.get("simplifiedGeometryGeoJSON") or meta.get("gjDownloadURL")
    fc = client.get(gj_url, timeout=300, follow_redirects=True).json()
    cfg = get_config()
    feats = []
    for f in fc.get("features", []):
        name = (f.get("properties") or {}).get("shapeName")
        did = match_district([name], cfg.districts) if name else None
        if did:
            g = shape(f["geometry"])
            feats.append({"type": "Feature", "geometry": mapping(g if g.is_valid else g.buffer(0)),
                          "properties": {"district_id": did, "name": name}})
    return feats, [f"geoBoundaries release {meta.get('boundaryYearRepresented')}"], {
        "url": gj_url, "licence": meta.get("boundaryLicense"), "source_detail": meta.get("boundarySource")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["osm", "geoboundaries", "auto"], default="auto")
    args = ap.parse_args()
    s = get_settings()
    client = httpx.Client(headers={"User-Agent": s.user_agent})
    feats, notes, prov = [], [], {}
    if args.source in ("osm", "auto"):
        try:
            feats, notes = from_osm(client, s.overpass_url)
            prov = {"source": "OpenStreetMap (Overpass API)", "url": s.overpass_url, "licence": "ODbL 1.0 — © OpenStreetMap contributors"}
        except Exception as exc:  # noqa: BLE001
            print(f"OSM failed: {exc}", file=sys.stderr)
    if not feats and args.source in ("geoboundaries", "auto"):
        feats, notes, extra = from_geoboundaries(client)
        prov = {"source": "geoBoundaries gbOpen IND ADM2", **extra}
    # keep the largest polygon per district if duplicates appear
    by_id: dict[str, dict] = {}
    for f in feats:
        did = f["properties"]["district_id"]
        if did not in by_id or shape(f["geometry"]).area > shape(by_id[did]["geometry"]).area:
            by_id[did] = f
    missing = [d.id for d in get_config().districts if d.id not in by_id]
    out = {"type": "FeatureCollection", "features": list(by_id.values())}
    path = boundaries_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, separators=(",", ":"))
    path.write_text(raw, encoding="utf-8")
    prov.update({"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                 "districts_matched": len(by_id), "districts_missing": missing, "notes": notes})
    (s.data_dir / PROVENANCE_FILE).write_text(json.dumps(prov, indent=2), encoding="utf-8")
    print(f"wrote {path} — {len(by_id)} districts; missing: {missing or 'none'}")
    return 0 if by_id else 1


if __name__ == "__main__":
    raise SystemExit(main())
