import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { CATEGORY_LABEL, istTime, num } from "../lib/format";
import { MapView } from "./MapView";
import { ErrorBox, Loading, Stat } from "./ui";

const friendly = (e: string) => (/\b429\b|limit exceeded/i.test(e) ? "elevation service rate limit reached; try again later" : e);

export function ImpactPanel({ district }: { district: any }) {
  const { data, error, loading, setData } = useApi<any>(`/api/districts/${district.id}/impact`);
  const [busy, setBusy] = useState(false);
  const layers = useMemo(() => ({
    cells: (data?.cells || []).map((c: any) => ({ lat: c.lat, lon: c.lon, cls: c.class, label: `Impact ${c.class} · ${num(c.elevation_m, 0)} m` })),
    areas: (data?.areas || []).filter((a: any) => a.class === "high" || a.class === "medium").map((a: any, i: number) => ({ id: `a${i}`, name: a.name, lat: a.lat, lon: a.lon })),
  }), [data]);
  const refresh = async () => {
    setBusy(true);
    try { setData(await api(`/api/districts/${district.id}/impact?refresh=true`)); } finally { setBusy(false); }
  };
  if (loading && !data) return <Loading label="Computing impact areas…" />;
  if (error && !data) return <ErrorBox error={error} what="impact areas" />;
  if (!data) return null;
  const h = data.hazard || {};
  const w = data.wind || {};
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="grid two">
        <div className="card">
          <div className="row"><div className="section-title grow">Hazard</div>
            <button className="btn xs ghost" onClick={refresh} disabled={busy} title="Recompute"><RefreshCw size={12} className={busy ? "spin" : ""} /></button></div>
          <div className="row wrap" style={{ gap: 14 }}>
            <Stat value={`${Math.round((data.hazard_index || 0) * 100)}%`} label="hazard index" />
            <Stat value={data.counts.high} label="high-impact cells" color="#ef4444" />
            <Stat value={data.counts.medium} label="medium" color="#f97316" />
          </div>
          {data.hazard_drivers?.length > 0 && <div className="tiny muted" style={{ marginTop: 4 }}>{data.hazard_drivers.join(" · ")}</div>}
          <table className="tbl" style={{ marginTop: 10 }}>
            <tbody>
              {(h.intensity || []).map((r: any, i: number) => (
                <tr key={i}><td>{CATEGORY_LABEL[r.category] || r.category} ≥ {r.threshold_mm} mm/24 h · ≤ {r.within_h} h</td>
                  <td className="mono" style={{ textAlign: "right" }}>{r.probability_pct}% <span className="muted">({r.members}/{r.valid})</span></td></tr>
              ))}
              <tr><td>Wind ≥ {w.cyclonic_kmh ?? 62} km/h · ≤ 72 h</td>
                <td className="mono" style={{ textAlign: "right" }}>{w.available ? <>{Math.round(w.p_cyclonic * 100)}% <span className="muted">({w.n_cyclonic}/{w.members})</span></> : <span className="muted">unavailable</span>}</td></tr>
              {w.available && <tr><td>Peak wind (ensemble median / max)</td><td className="mono" style={{ textAlign: "right" }}>{w.peak_kmh_median} / {w.peak_kmh_max} km/h</td></tr>}
              <tr><td>Rain, last 72 h</td><td className="mono" style={{ textAlign: "right" }}>{h.antecedent_72h_mm ?? "—"} mm {h.saturated ? <span className="pill warn">saturated</span> : null}</td></tr>
            </tbody>
          </table>
          <div className="tiny faint" style={{ marginTop: 6 }}>{h.model} {h.model_run ? `run ${h.model_run}` : ""} · {istTime(data.computed_at, true)}</div>
        </div>
        <div className="card" style={{ padding: 0, overflow: "hidden", minHeight: 320 }}>
          <div className="mapwrap" style={{ height: 340 }}>
            <MapView districts={[{ id: district.id, name: district.name, hq: district.hq, level: district.level ?? null, geometry: district.geometry }]}
              layers={layers} center={[district.hq[1], district.hq[0]]} zoom={9} />
          </div>
        </div>
      </div>
      {data.terrain_error && <div className="banner warn small">Terrain unavailable: {friendly(data.terrain_error)}</div>}
      {data.areas?.length > 0 && (
        <div className="card">
          <div className="section-title">Most affected areas</div>
          <table className="tbl">
            <thead><tr><th>Area</th><th>Impact</th><th>Elevation</th><th>Why</th><th>Settlement</th></tr></thead>
            <tbody>
              {data.areas.map((a: any) => (
                <tr key={a.name}>
                  <td><span className={`cls ${a.class}`} /> {a.name}</td>
                  <td className="mono">{a.class} <span className="muted">{Math.round(a.impact * 100)}%</span></td>
                  <td className="mono">{num(a.elevation_m, 0)} m</td>
                  <td className="small">{a.factors.join(" · ") || "—"}</td>
                  <td className="small">{a.settlement}{a.population ? ` · pop. ${a.population}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
