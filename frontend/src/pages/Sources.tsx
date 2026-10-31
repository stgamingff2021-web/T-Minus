import { useState } from "react";
import { FlaskConical, Radio } from "lucide-react";
import { Sparkline } from "../components/charts";
import { ErrorBox, Loading } from "../components/ui";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { useApi } from "../lib/hooks";
import { ago, istTime } from "../lib/format";

const DOT: Record<string, string> = { live: "live", stale: "stale", failed: "failed", "simulated outage": "sim", "never fetched": "off", disabled: "off" };

export default function Sources() {
  const { data, error, loading, reload } = useApi<any>("/api/sources", 10000);
  const fetches = useApi<any>("/api/fetches?limit=40", 10000);
  const health = useApi<any>("/api/health", 15000);
  const { user, requireLogin, toast } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const isAdmin = user?.role === "admin";

  const toggle = async (key: string, enabled: boolean) => {
    if (!requireLogin()) return;
    setBusy(key);
    try {
      await api("/api/admin/chaos", { method: "POST", json: { source: key, enabled } });
      toast({ kind: enabled ? "warn" : "ok", title: enabled ? `Simulated outage on: ${key}` : `Restored: ${key}` });
      reload();
    } catch (e) {
      toast({ kind: "danger", title: "Chaos switch refused", body: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page">
      <div className="hero">
        <div>
          <h1><Radio size={20} /> Sources</h1>
        </div>
      </div>
      {health.data && (
        <div className="grid three" style={{ marginBottom: 14 }}>
          <div className="card tight"><div className="section-title">Scheduler</div>{health.data.scheduler.alive ? <span className="pill ok">running · every {health.data.scheduler.interval_min} min</span> : <span className="pill warn">{health.data.scheduler.enabled ? "not alive" : "disabled"}</span>}<div className="tiny muted">next {istTime(health.data.scheduler.next_tick, true)}</div></div>
          <div className="card tight"><div className="section-title">Last scan</div>{health.data.last_scan ? <><b>{health.data.last_scan.status}</b> · {istTime(health.data.last_scan.finished_at, true)}<div className="tiny muted">{health.data.last_scan.districts_ok} ok · {health.data.last_scan.districts_failed} failed · {health.data.last_scan.duration_s}s</div></> : <span className="muted">none yet</span>}</div>
          <div className="card tight"><div className="section-title">Knowledge base</div><b>{health.data.plan_documents}</b> plan document(s)<div className="tiny muted">{health.data.boundaries_loaded} district boundaries</div></div>
        </div>
      )}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row" style={{ marginBottom: 8 }}>
          <div className="section-title grow" style={{ margin: 0 }}>Live sources</div>
          {isAdmin && <span className="pill violet"><FlaskConical size={12} /> outage simulator</span>}
        </div>
        {loading && !data && <Loading label="Reading source status…" />}
        <ErrorBox error={error} what="source status" />
        {data && (
          <div className="scroll">
            <table className="t">
              <thead><tr><th>Status</th><th>Source</th><th>Last fetch</th><th>Latency</th><th>Model run</th><th>Last error</th>{isAdmin && <th>Simulate outage</th>}</tr></thead>
              <tbody>
                {data.sources.filter((s: any) => s.status !== "disabled").map((s: any) => (
                  <tr key={s.key}>
                    <td><span className="row" style={{ gap: 6 }}><span className={`dot ${DOT[s.status] || "off"}`} /> <b className="small">{s.status}</b></span></td>
                    <td><div><b>{s.name}</b></div><div className="tiny muted">{s.provider}</div></td>
                    <td className="small">{s.last_fetch_at ? <>{istTime(s.last_fetch_at, true)}<div className="tiny muted">{ago(s.last_fetch_at)} · {s.calls} calls · {s.failures} failed</div></> : <span className="faint">—</span>}</td>
                    <td><div className="mono small">{s.latency_ms != null ? `${s.latency_ms} ms` : "—"}</div><Sparkline values={s.latency_history || []} /></td>
                    <td className="mono tiny">{s.model_run || "—"}</td>
                    <td className="tiny" style={{ color: s.last_error ? "var(--act)" : undefined, maxWidth: 260 }}>{s.last_error || ""}</td>
                    {isAdmin && <td>{s.chaos_allowed && <button className={`switch ${s.status === "simulated outage" ? "on" : ""}`} disabled={!isAdmin || busy === s.key} title={isAdmin ? "Force this source to fail" : "Admin only"} onClick={() => toggle(s.key, s.status !== "simulated outage")} aria-label={`Simulate outage for ${s.name}`} />}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <div className="card">
        <div className="section-title">Fetch log</div>
        <table className="t small">
          <thead><tr><th>Time</th><th>Source</th><th>Status</th><th>HTTP</th><th>Latency</th><th>Size</th><th>Fetch</th></tr></thead>
          <tbody>
            {(fetches.data?.fetches || []).map((f: any) => (
              <tr key={f.id}>
                <td className="mono">{istTime(f.fetched_at, true)}</td>
                <td>{f.source}</td>
                <td>{f.simulated ? <span className="pill violet">simulated outage</span> : f.status === "ok" ? <span className="pill ok">ok</span> : <span className="pill danger" title={f.error}>{f.status}</span>}</td>
                <td className="mono">{f.http_code ?? "—"}</td>
                <td className="mono">{f.latency_ms ?? "—"} ms</td>
                <td className="mono">{f.bytes ? `${(f.bytes / 1024).toFixed(0)} KB` : "—"}</td>
                <td><a className="mono" href={`/api/fetches/${f.id}?include_payload=true`} target="_blank" rel="noreferrer">{f.id}</a></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!(fetches.data?.fetches || []).length && <div className="empty small">No API calls logged yet.</div>}
      </div>
    </div>
  );
}
