import { useMemo, useState } from "react";
import { useApi } from "../lib/hooks";
import { num } from "../lib/format";
import { MapView } from "./MapView";
import { ErrorBox, Loading, SourceBadge } from "./ui";

const RISK = ["high", "medium", "low"] as const;

export function AssetsPanel({ district }: { district: any }) {
  const { data, error, loading } = useApi<any>(`/api/districts/${district.id}/assets`);
  const [risk, setRisk] = useState<string>("");
  const layers = useMemo(() => ({
    facilities: (data?.assets || []).map((a: any) => ({ id: a.id, name: `${a.name || a.label} · ${a.risk} risk`, type: a.type, lat: a.lat, lon: a.lon })),
  }), [data]);
  if (loading && !data) return <Loading label="Checking critical assets…" />;
  if (error && !data) return <ErrorBox error={error} what="critical assets" />;
  if (!data) return null;
  const shown = (data.assets || []).filter((a: any) => !risk || a.risk === risk);
  return (
    <div className="col" style={{ gap: 12 }}>
      {data.osm_error && <div className="banner warn small">{/timeout|timed out/i.test(data.osm_error) ? "OpenStreetMap did not respond; try again later" : data.osm_error}</div>}
      <div className="grid two">
        <div className="card">
          <div className="section-title">Assets at risk</div>
          <div className="counts">
            {RISK.map((r) => (
              <button key={r} onClick={() => setRisk(risk === r ? "" : r)}>
                <div className={`stat ${risk === r ? "on" : ""}`}><b style={{ color: r === "high" ? "#ef4444" : r === "medium" ? "#f97316" : undefined }}>{data.counts[r]}</b><span>{r}</span></div>
              </button>
            ))}
          </div>
          {data.t_minus_h != null && <div className="tiny muted" style={{ marginTop: 6 }}>Expected impact in ~{num(data.t_minus_h, 0)} h</div>}
        </div>
        <div className="card" style={{ padding: 0, overflow: "hidden", minHeight: 260 }}>
          <div className="mapwrap" style={{ height: 280 }}>
            <MapView districts={[{ id: district.id, name: district.name, hq: district.hq, level: district.level ?? null, geometry: district.geometry }]}
              layers={layers} center={[district.hq[1], district.hq[0]]} zoom={9} />
          </div>
        </div>
      </div>
      <div className="card">
        {!shown.length ? <div className="empty small">{data.assets?.length ? "None in this group." : "No mapped assets found."}</div> : (
          <table className="tbl">
            <thead><tr><th>Asset</th><th>Risk</th><th>Why</th><th>Measures</th></tr></thead>
            <tbody>
              {shown.map((a: any) => (
                <tr key={a.id}>
                  <td><b>{a.name || "Unnamed"}</b><div className="tiny muted">{a.label}{a.elevation_m != null ? ` · ${num(a.elevation_m, 0)} m` : ""}</div></td>
                  <td><span className={`chip ${a.risk === "high" ? "unavailable" : a.risk === "medium" ? "unverified" : "ready"}`}>{a.risk}{a.urgent ? " · <24 h" : ""}</span></td>
                  <td className="small">{a.reasons.join(" · ") || "—"}</td>
                  <td className="small">
                    {a.measures.length > 0 && <><SourceBadge source="suggested" /><ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>{a.measures.map((m: string) => <li key={m}>{m}</li>)}</ul></>}
                    {a.relocation?.length > 0 && (
                      <div style={{ marginTop: 4 }}>Move to: {a.relocation.map((o: any) => `${o.name} (${o.distance_km} km, ${o.impact_class})`).join(" · ")}
                        <div className="tiny faint">straight-line distance · route not checked</div></div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {data.plan_assets?.length > 0 && (
        <div className="card">
          <div className="section-title">Named in the plan (not mapped)</div>
          {data.plan_assets.slice(0, 40).map((p: any, i: number) => (
            <div key={i} className="rec"><div className="what">{p.label}</div><span className="tiny muted">{String(p.type || "").replace("_", " ")} · p. {p.page}</span></div>
          ))}
        </div>
      )}
    </div>
  );
}
