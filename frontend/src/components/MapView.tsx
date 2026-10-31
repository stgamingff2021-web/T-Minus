import { useEffect, useRef, useState } from "react";
import maplibregl, { Map as MLMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useApp } from "../lib/app";

export const LEVEL_COLOR: Record<string, string> = {
  ACT: "#ef4444",
  PREPARE: "#f97316",
  WATCH: "#eab308",
  QUIET: "#22c55e",
  NODATA: "#64748b",
};

const STYLE_URL = {
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
};

/** Blank style used when the basemap cannot be fetched — our own layers still render. */
const FALLBACK_STYLE = (dark: boolean): StyleSpecification => ({
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": dark ? "#0a1017" : "#e9edf2" } }],
});

export type MapDistrict = {
  id: string;
  name: string;
  hq: [number, number];
  level: string | null;
  noData?: boolean;
  geometry?: any;
  label?: string;
};

export type MapLayers = {
  facilities?: { id: string; name?: string; type: string; lat: number; lon: number }[];
  areas?: { id: string; name: string; lat: number; lon: number }[];
  routes?: { id: string; geometry: any; distance_m?: number; duration_s?: number }[];
};

function districtsGeoJSON(ds: MapDistrict[]) {
  const polys: any[] = [];
  const points: any[] = [];
  for (const d of ds) {
    const lvl = d.noData ? "NODATA" : d.level || "QUIET";
    const props = { id: d.id, name: d.name, level: lvl, color: LEVEL_COLOR[lvl], label: d.label || "" };
    if (d.geometry) polys.push({ type: "Feature", geometry: d.geometry, properties: props });
    points.push({ type: "Feature", geometry: { type: "Point", coordinates: [d.hq[1], d.hq[0]] }, properties: props });
  }
  return { polys: { type: "FeatureCollection", features: polys }, points: { type: "FeatureCollection", features: points } };
}

export function MapView({
  districts, layers, onSelect, center = [79.0, 11.1], zoom = 6.1, height = "100%", focus,
}: {
  districts: MapDistrict[];
  layers?: MapLayers;
  onSelect?: (id: string) => void;
  center?: [number, number];
  zoom?: number;
  height?: string | number;
  focus?: [number, number] | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const markersRef = useRef<maplibregl.Marker[]>([]);
  const [ready, setReady] = useState(false);
  const [styleGen, setStyleGen] = useState(0);
  const [basemapFailed, setBasemapFailed] = useState(false);
  const handlersRef = useRef(false);
  const { theme } = useApp();

  useEffect(() => {
    if (!ref.current) return;
    let fellBack = false;
    const map = new maplibregl.Map({
      container: ref.current,
      style: STYLE_URL[theme],
      center,
      zoom,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    const timer = window.setTimeout(() => {
      if (!map.isStyleLoaded() && !fellBack) {
        fellBack = true;
        setBasemapFailed(true);
        map.setStyle(FALLBACK_STYLE(theme === "dark"));
      }
    }, 6000);
    map.on("error", (e: any) => {
      const msg = String(e?.error?.message || "");
      if (!fellBack && /style|Failed to fetch|NetworkError/i.test(msg) && !map.isStyleLoaded()) {
        fellBack = true;
        setBasemapFailed(true);
        map.setStyle(FALLBACK_STYLE(theme === "dark"));
      }
    });
    map.on("style.load", () => {
      setReady(true);
      setStyleGen((g) => g + 1);
    });
    map.on("load", () => setReady(true));
    return () => {
      window.clearTimeout(timer);
      map.remove();
      mapRef.current = null;
      handlersRef.current = false;
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  // district layers
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const apply = () => {
      const { polys, points } = districtsGeoJSON(districts);
      const upsert = (id: string, data: any) => {
        const src = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
        if (src) src.setData(data);
        else map.addSource(id, { type: "geojson", data });
      };
      upsert("d-polys", polys);
      upsert("d-points", points);
      const hasGlyphs = !!(map.getStyle() as any)?.glyphs;
      if (!map.getLayer("d-fill")) {
        map.addLayer({ id: "d-fill", type: "fill", source: "d-polys", paint: { "fill-color": ["get", "color"], "fill-opacity": ["match", ["get", "level"], "QUIET", 0.12, "NODATA", 0.06, 0.38] } });
        map.addLayer({ id: "d-line", type: "line", source: "d-polys", paint: { "line-color": ["get", "color"], "line-width": ["match", ["get", "level"], "QUIET", 0.6, "NODATA", 0.5, 1.8], "line-opacity": 0.8 } });
        map.addLayer({
          id: "d-dot", type: "circle", source: "d-points",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, ["match", ["get", "level"], "ACT", 7, "PREPARE", 6, "WATCH", 5, 3.5], 9, ["match", ["get", "level"], "ACT", 12, "PREPARE", 10, "WATCH", 9, 6]],
            "circle-color": ["get", "color"], "circle-stroke-color": "#0b1118", "circle-stroke-width": 1.5,
          },
        });
        if (hasGlyphs) map.addLayer({
          id: "d-label", type: "symbol", source: "d-points", minzoom: 6.3,
          layout: { "text-field": ["get", "name"], "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top", "text-optional": true },
          paint: { "text-color": theme === "dark" ? "#cbd5e1" : "#1e293b", "text-halo-color": theme === "dark" ? "#05080c" : "#ffffff", "text-halo-width": 1.2 },
        });
      }
      if (!handlersRef.current) {
        handlersRef.current = true;
        const click = (e: any) => {
          const f = e.features?.[0];
          if (f && onSelect) onSelect(f.properties.id);
        };
        map.on("click", "d-fill", click);
        map.on("click", "d-dot", click);
        for (const l of ["d-fill", "d-dot"]) {
          map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
          map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
        }
        const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
        map.on("mousemove", "d-dot", (e: any) => {
          const p = e.features?.[0]?.properties;
          if (!p) return;
          popup.setLngLat(e.lngLat).setHTML(`<b>${p.name}</b><br/><span style="color:${p.color};font-weight:800">${p.level === "NODATA" ? "NO DATA" : p.level}</span>${p.label ? `<br/><span style="opacity:.75">${p.label}</span>` : ""}`).addTo(map);
        });
        map.on("mouseleave", "d-dot", () => popup.remove());
      }
      // pulsing HTML markers for ACT districts (§7.2: ACT districts pulse)
      markersRef.current.forEach((m) => m.remove());
      markersRef.current = districts
        .filter((d) => d.level === "ACT" && !d.noData)
        .map((d) => {
          const el = document.createElement("div");
          el.className = "pulse-marker";
          el.title = `${d.name} — ACT`;
          el.onclick = () => onSelect?.(d.id);
          return new maplibregl.Marker({ element: el }).setLngLat([d.hq[1], d.hq[0]]).addTo(map);
        });
    };
    if (map.isStyleLoaded()) apply();
    else map.once("idle", apply);
  }, [districts, ready, onSelect, theme, styleGen]);

  // run layers: facilities, at-risk areas, routes
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !layers) return;
    const apply = () => {
      const fac = {
        type: "FeatureCollection",
        features: (layers.facilities || []).map((f) => ({ type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] }, properties: { name: f.name || f.type, type: f.type } })),
      };
      const areas = {
        type: "FeatureCollection",
        features: (layers.areas || []).map((a) => ({ type: "Feature", geometry: { type: "Point", coordinates: [a.lon, a.lat] }, properties: { name: a.name } })),
      };
      const routes = { type: "FeatureCollection", features: (layers.routes || []).map((r) => ({ type: "Feature", geometry: r.geometry, properties: { id: r.id } })) };
      const upsert = (id: string, data: any) => {
        const src = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
        if (src) src.setData(data as any);
        else map.addSource(id, { type: "geojson", data: data as any });
      };
      upsert("r-fac", fac);
      upsert("r-areas", areas);
      upsert("r-routes", routes);
      if (!map.getLayer("r-routes")) {
        map.addLayer({ id: "r-routes", type: "line", source: "r-routes", paint: { "line-color": "#38bdf8", "line-width": 4, "line-opacity": 0.85 } });
        map.addLayer({ id: "r-areas", type: "circle", source: "r-areas", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 8, 14, 40], "circle-color": "#ef4444", "circle-opacity": 0.25, "circle-stroke-color": "#ef4444", "circle-stroke-width": 1.5 } });
        map.addLayer({
          id: "r-fac", type: "circle", source: "r-fac",
          paint: { "circle-radius": 5, "circle-stroke-width": 1.5, "circle-stroke-color": "#0b1118", "circle-color": ["match", ["get", "type"], "hospital", "#f472b6", "clinic", "#f9a8d4", "shelter", "#22c55e", "school", "#a78bfa", "community_centre", "#34d399", "fire_station", "#fb923c", "police", "#60a5fa", "#94a3b8"] },
        });
        const popup = new maplibregl.Popup({ closeButton: false, offset: 8 });
        for (const l of ["r-fac", "r-areas"]) {
          map.on("mousemove", l, (e: any) => {
            const p = e.features?.[0]?.properties;
            if (p) popup.setLngLat(e.lngLat).setHTML(`<b>${p.name}</b>${p.type ? `<br/><span style="opacity:.75">${p.type}</span>` : "<br/><span style='opacity:.75'>at-risk area (from plan)</span>"}`).addTo(map);
          });
          map.on("mouseleave", l, () => popup.remove());
        }
      }
    };
    if (map.isStyleLoaded()) apply();
    else map.once("idle", apply);
  }, [layers, ready, styleGen]);

  useEffect(() => {
    if (focus && mapRef.current) mapRef.current.flyTo({ center: [focus[1], focus[0]], zoom: 9.2, speed: 1.4 });
  }, [focus]);

  return (
    <div style={{ position: "relative", height, width: "100%" }}>
      <div ref={ref} className="map" />
      {basemapFailed && (
        <div className="pill warn" style={{ position: "absolute", top: 8, right: 8, zIndex: 3 }}>
          Basemap unavailable — showing district layers only
        </div>
      )}
    </div>
  );
}
