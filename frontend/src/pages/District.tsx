import { useState } from "react";
import { Link } from "react-router-dom";
import { useGo, useRouteId } from "../lib/links";
import { AlertTriangle, BookOpenText, Bot, Calculator, Droplets, FileSearch, History, Loader2, Waves, XCircle } from "lucide-react";
import { AssetsPanel } from "../components/AssetsPanel";
import { FuturesChart, RiverChart } from "../components/charts";
import { ImpactPanel } from "../components/ImpactPanel";
import { PlanDataPanel } from "../components/PlanDataPanel";
import { TasksTable } from "../components/TasksTable";
import { ConfirmDialog, Countdown, ErrorBox, LevelBadge, Loading, Modal } from "../components/ui";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { useApi } from "../lib/hooks";
import { ago, CATEGORY_LABEL, istTime, num, pct } from "../lib/format";

const CAT_ORDER: Record<string, number> = { very_heavy: 0, extremely_heavy: 1, heavy: 2 };

function RecomputeModal({ assessmentId, onClose }: { assessmentId: string; onClose: () => void }) {
  const { data, error, loading } = useApi<any>(`/api/assessments/${assessmentId}/recompute`);
  return (
    <Modal title="Recompute from raw data" onClose={onClose} wide>
      {loading && <Loading label="Recomputing…" />}
      <ErrorBox error={error} what="the recomputation" />
      {data && (
        <>
          <div className={`banner ${data.all_match ? "ok" : "danger"}`}>
            {data.all_match ? "✓ All probabilities match the stored raw response." : "✗ Stored and recomputed values differ."}
          </div>
          <div className="small">
            <b>Formula:</b> <span className="mono">{data.formula}</span>
          </div>
          <div className="small muted">
            Source fetch <span className="mono">{data.fetch.id}</span> · {data.model} · fetched {istTime(data.fetch.fetched_at, true)} · SHA-256 <span className="mono">{data.fetch.sha256?.slice(0, 16)}…</span> · {(data.fetch.bytes / 1e6).toFixed(2)} MB ·{" "}
            <a href={`/api/fetches/${data.fetch.id}?include_payload=true`} target="_blank" rel="noreferrer">open raw JSON</a>
          </div>
          <table className="t">
            <thead><tr><th>Rule window</th><th>Stored</th><th>Recomputed</th><th>Match</th></tr></thead>
            <tbody>
              {data.rows.map((r: any) => (
                <tr key={r.key}>
                  <td className="mono">{r.key} (≥ {r.threshold_mm} mm within {r.within_h} h)</td>
                  <td className="mono">{r.stored.n_exceed}/{r.stored.n_valid} = {pct(r.stored.probability)}</td>
                  <td className="mono">{r.recomputed.n_exceed}/{r.recomputed.n_valid} = {pct(r.recomputed.probability)}</td>
                  <td>{r.match ? <span className="pill ok">match</span> : <span className="pill danger">differs</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <details>
            <summary className="small">Member maxima ({data.rows[0]?.key})</summary>
            <div className="mono tiny" style={{ columns: 3, marginTop: 8 }}>
              {Object.entries(data.rows[0]?.member_max_24h_mm || {}).map(([k, v]: any) => (
                <div key={k} style={{ color: v !== null && v >= data.rows[0].threshold_mm ? "var(--prepare)" : undefined }}>{k.replace("precipitation", "ctl").replace("_member", "m")}: {v === null ? "missing" : `${v} mm`}</div>
              ))}
            </div>
          </details>
        </>
      )}
    </Modal>
  );
}

export default function District() {
  const route = useRouteId("district");
  const id = route.id;
  const { data, error, loading } = useApi<any>(id ? `/api/districts/${id}` : null, 30000);
  const { requireLogin, toast, user } = useApp();
  const go = useGo();
  const [busy, setBusy] = useState(false);
  const [recompute, setRecompute] = useState(false);
  const [closing, setClosing] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [tab, setTab] = useState("forecast");

  if (route.error) return <div className="page"><ErrorBox error={route.error} what="this district" /></div>;
  if (!id || (loading && !data)) return <div className="page"><Loading label="Loading…" /></div>;
  if (error && !data) return <div className="page"><ErrorBox error={error} what="this district" /></div>;
  if (!data) return null;
  const d = data.district;
  const rain = data.rain;
  const head = rain?.headline;
  const flags = rain?.flags || {};

  const runPlanner = async (hazard = "rain") => {
    if (!requireLogin()) return;
    setBusy(true);
    try {
      const r = await api(`/api/districts/${id}/plan`, { method: "POST", json: { hazard } });
      go("run", r.run_id);
    } catch (e) {
      toast({ kind: "danger", title: "Planner not started", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const closeTrigger = async () => {
    if (!closing) return;
    try {
      await api(`/api/triggers/${closing}/close`, { method: "POST", json: { confirm: true, note } });
      toast({ kind: "ok", title: "Trigger closed (CAP msgType = Cancel)" });
      setClosing(null);
    } catch (e) {
      toast({ kind: "danger", title: "Could not close", body: (e as Error).message });
    }
  };

  return (
    <div className="page">
      <div className="hero">
        <div>
          <div className="small muted"><Link to="/">Command map</Link> / {d.state}</div>
          <h1>{d.name}</h1>
        </div>
        <LevelBadge level={d.level} big noData={!rain && !d.level} />
        {d.level && d.t_star && (
          <div className="big-cd">
            <Countdown target={d.t_star} />
            <small>TO EXPECTED IMPACT · {istTime(d.t_star, true)}</small>
          </div>
        )}
        <div className="row right wrap">
          {rain && <button className="btn" onClick={() => setRecompute(true)}><Calculator size={15} /> Recompute</button>}
          <button className="btn primary" onClick={() => runPlanner("rain")} disabled={busy}>
            {busy ? <Loader2 size={15} className="spin" /> : <Bot size={15} />} Run planner
          </button>
        </div>
      </div>

      <div className="col" style={{ marginBottom: 14 }}>
        {rain?.stale && <div className="banner warn"><AlertTriangle size={16} /> Stale · updated {istTime(rain.data_time, true)} ({ago(rain.data_time)}){rain.insufficient ? " · too old for new triggers" : ""}</div>}
        {rain?.degraded && <div className="banner warn"><AlertTriangle size={16} /> Degraded · {rain.n_valid}/{rain.n_members} members valid</div>}
        {flags.disagreement && <div className="banner warn"><AlertTriangle size={16} /> {(flags.notes || []).find((n: string) => n.startsWith("Sources disagree")) || "Sources disagree"}</div>}
        {flags.official_available === false && <div className="banner info">Official alerts unavailable</div>}
        {(flags.fallback_from || []).length > 0 && <div className="banner info">Using {rain.model} · primary ensemble failed</div>}
        {!d.has_plan && <div className="banner info"><BookOpenText size={16} /> No district plan · <Link to="/plans">Add plan</Link></div>}
        {!rain && <div className="banner warn">No live assessment yet</div>}
      </div>

      <div className="tabs">
        {[["forecast", "Forecast"], ["impact", "Impact areas"], ["assets", "Critical assets"], ["plan", "Plan data"], ["tasks", "Tasks"]].map(([k, label]) => (
          <button key={k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      {tab === "impact" && <ImpactPanel district={d} />}
      {tab === "assets" && <AssetsPanel district={d} />}
      {tab === "plan" && <PlanDataPanel districtId={d.id} />}
      {tab === "tasks" && <div className="card"><TasksTable districtId={d.id} showDistrict={false} /></div>}
      {tab === "forecast" && (<>
      {rain && (
        <div className="grid two" style={{ marginBottom: 14 }}>
          <div className="card hazard-card">
            <div className="section-title"><Droplets size={12} /> Rain · {rain.model} · run {rain.model_run_id}</div>
            {head?.probability != null ? (
              <>
                <div className="headline">{CATEGORY_LABEL[head.category]} (≥ {head.threshold_mm} mm in 24 h)</div>
                <div className="row wrap" style={{ alignItems: "baseline", gap: 14 }}>
                  <span className="prob">{pct(head.probability)}</span>
                  <span className="muted">({head.n_exceed} of {head.n_valid} forecasts) within {head.within_h} h</span>
                </div>
                <div className="small">
                  {head.t_minus_h != null ? <>Expected in <b>~{num(head.t_minus_h, 0)} h</b> ({istTime(rain.t_star, true)}) · earliest onset <b>~{num(head.onset_h, 0)} h</b></> : "No expected impact time."}
                </div>
              </>
            ) : (
              <div className="headline">No member reaches {CATEGORY_LABEL.heavy.toLowerCase()} in 5 days</div>
            )}
            <div className="divider" />
            <table className="t small">
              <thead><tr><th>Rule</th><th>Probability</th><th>Level rule</th></tr></thead>
              <tbody>
                {Object.entries(rain.probabilities || {}).sort(([, a]: any, [, b]: any) => (CAT_ORDER[a.category] - CAT_ORDER[b.category]) || (a.within_h - b.within_h)).map(([k, p]: any) => {
                  const met = (rain.rules_met || []).some((r: any) => `${r.category}@${Math.round(r.within_h)}h` === k);
                  return (
                    <tr key={k}>
                      <td className="mono">{CATEGORY_LABEL[p.category]} ≥ {p.threshold_mm} mm · ≤{p.within_h} h{p.truncated ? " (truncated)" : ""}</td>
                      <td className="mono">{pct(p.probability)} <span className="muted">({p.n_exceed} of {p.n_valid})</span></td>
                      <td>{met ? <span className="pill danger">met</span> : <span className="faint">—</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="tiny muted" style={{ marginTop: 6 }}>
              Data fetched {istTime(rain.data_time, true)} · computed {ago(rain.computed_at)} · fetch {rain.fetch_ids?.[0] && <a href={`/api/fetches/${rain.fetch_ids[0]}`} target="_blank" rel="noreferrer" className="mono">{rain.fetch_ids[0]}</a>}
            </div>
          </div>
          <div className="card">
            <div className="section-title">Level</div>
            <dl className="kv">
              <dt>Ensemble base level</dt><dd><LevelBadge level={rain.base_level} /></dd>
              <dt>Official alerts</dt>
              <dd>{(flags.official_alerts || []).length ? flags.official_alerts.map((a: any, i: number) => (
                <div key={i}><span className={`pill ${a.color === "red" ? "danger" : "warn"}`}>{a.color || a.severity}</span> {a.agency} · {a.event} · issued {istTime(a.issued, true)} · expires {istTime(a.expires, true)}</div>
              )) : <span className="muted">{flags.official_available === false ? "unavailable" : "none active for this district"}</span>}</dd>
              <dt>Floor</dt><dd>{flags.floor_level ? <LevelBadge level={flags.floor_level} /> : <span className="muted">none</span>}</dd>
              <dt>Antecedent rain (72 h)</dt><dd className="mono">{flags.antecedent?.mm_72h ?? "—"} mm {flags.saturated_ground ? <span className="pill warn">saturated ground</span> : null}</dd>
              <dt>River (GloFAS)</dt><dd>{flags.river_level ? <LevelBadge level={flags.river_level} /> : <span className="muted">below WATCH / no data</span>} {flags.compound ? <span className="pill warn">compound hazard</span> : null}</dd>
              <dt>Escalation (+1 max)</dt><dd>{(flags.escalations || []).join(", ") || <span className="muted">none</span>}</dd>
              <dt>Resolved level</dt><dd><LevelBadge level={rain.level} /></dd>
              <dt>Hysteresis level</dt><dd><LevelBadge level={rain.hysteresis_level} /></dd>
            </dl>
            <ul className="small muted" style={{ marginBottom: 0 }}>{(flags.notes || []).map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>
          </div>
        </div>
      )}

      {data.futures && head && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="row">
            <div className="section-title grow">{data.futures.members.length} forecasts · 24-h rain</div>
            <span className="tiny muted">{data.futures.model} · fetched {istTime(data.futures.fetched_at, true)}</span>
          </div>
          <FuturesChart data={data.futures} threshold={head.threshold_mm} windowH={head.within_h} tStar={head.t_star} onset={head.onset} />
          <div className="tiny muted">orange: above {head.threshold_mm} mm · blue: {head.within_h} h window · red: expected impact</div>
        </div>
      )}

      <div className="grid two" style={{ marginBottom: 14 }}>
        <div className="card">
          <div className="section-title"><History size={12} /> Triggers</div>
          {!(data.triggers || []).length && <div className="muted small">No triggers</div>}
          {(data.triggers || []).map((tr: any) => (
            <div key={tr.id} className="card tight" style={{ marginBottom: 8, background: "var(--panel2)" }}>
              <div className="row wrap">
                <LevelBadge level={tr.level} />
                <b>{tr.hazard}</b>
                <span className={`pill ${tr.status === "active" ? "danger" : tr.status === "closed" ? "" : "info"}`}>{tr.status.replace(/_/g, " ")}</span>
                <span className="tiny muted">CAP next: {tr.cap_msg_type}</span>
                {tr.status !== "closed" && <button className="btn xs right" onClick={() => user ? setClosing(tr.id) : requireLogin()}><XCircle size={12} /> Close (stand-down)</button>}
              </div>
              <div className="tiny muted" style={{ marginTop: 4 }}>
                opened {istTime(tr.opened_at, true)} · window {istTime(tr.window_start, true)} → {istTime(tr.window_end, true)}
                {tr.below_runs?.length ? ` · below exit for ${tr.below_runs.length} new run(s)` : ""}
              </div>
              <details className="tiny" style={{ marginTop: 4 }}>
                <summary>history ({tr.history.length})</summary>
                <ul>{tr.history.map((h: any, i: number) => <li key={i}><span className="mono">{istTime(h.ts, true)}</span> {h.action} → {h.level}/{h.status}: {h.reason}</li>)}</ul>
              </details>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="section-title"><Bot size={12} /> Planner runs</div>
          {!(data.runs || []).length && <div className="muted small">No runs yet</div>}
          <table className="t small">
            <tbody>
              {(data.runs || []).map((r: any) => (
                <tr key={r.id} style={{ cursor: "pointer" }} onClick={() => go("run", r.id)}>
                  <td className="mono">{istTime(r.started_at, true)}</td>
                  <td>{r.level ? <LevelBadge level={r.level} /> : null}</td>
                  <td><span className={`pill ${r.status === "completed" ? "ok" : r.status === "running" ? "info" : "warn"}`}>{r.status}</span></td>
                  <td className="muted">{r.started_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="divider" />
          <div className="section-title"><FileSearch size={12} /> Plan documents</div>
          {(data.plan_documents || []).map((p: any) => (
            <div key={p.id} className="small">📄 {p.title} {p.edition_year ? `(${p.edition_year})` : ""} · {p.pages} pp · {p.source_url ? <a href={p.source_url} target="_blank" rel="noreferrer">official source</a> : "uploaded"}</div>
          ))}
          {!(data.plan_documents || []).length && <div className="small muted">None · <Link to="/plans">Add plan</Link></div>}
        </div>
      </div>

      {data.river && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="section-title" title={data.river_caveat}><Waves size={12} /> River discharge (GloFAS)</div>
          <div className="grid two">
            {(data.river.points || []).map((p: any) => (
              <div key={p.point_id}>
                <div className="row small"><b>{p.river}</b>{p.also_represents?.length ? <span className="tiny muted">(same GloFAS cell as {p.also_represents.join(", ")})</span> : null} {p.level ? <LevelBadge level={p.level} /> : <span className="faint">below WATCH</span>} <span className="tiny muted right">{p.baseline ? `cell ${p.lat.toFixed(3)}, ${p.lon.toFixed(3)} · baseline ${p.baseline.period} · ${p.baseline.n_days} days` : p.note}</span></div>
                {p.rules && <div className="tiny muted">{Object.entries(p.rules).map(([lv, r]: any) => `${lv}: ${r.statistic.replace("ensemble_", "")} peak ${r.peak_m3s} vs P${r.percentile} ${r.threshold_m3s} m³/s ${r.exceeds ? "✓" : "—"}`).join(" · ")}</div>}
                <RiverChart chart={p.chart} p95={p.baseline?.p95} p99={p.baseline?.p99} />
              </div>
            ))}
          </div>
        </div>
      )}

      </>)}

      {recompute && rain && <RecomputeModal assessmentId={rain.id} onClose={() => setRecompute(false)} />}
      {closing && (
        <ConfirmDialog title="Confirm stand-down and close this trigger?" confirmLabel="Close trigger" danger onCancel={() => setClosing(null)} onConfirm={closeTrigger}>
          <p className="small" style={{ margin: 0 }}>Closes the trigger and issues a CAP Cancel.</p>
          <label className="field">Note (optional)<input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></label>
        </ConfirmDialog>
      )}
    </div>
  );
}
