import { useEffect, useMemo } from "react";
import { SecureLink, useRouteId } from "../lib/links";
import { AlertTriangle, CircleHelp, FileCheck2, MapPinned, ShieldCheck } from "lucide-react";
import { ActionTimeline } from "../components/ActionTimeline";
import { AgentTrace } from "../components/AgentTrace";
import { CommsPanel } from "../components/CommsPanel";
import { MapView } from "../components/MapView";
import { Countdown, ErrorBox, LevelBadge, Loading } from "../components/ui";
import { useApp } from "../lib/app";
import { useApi, useRunTrace } from "../lib/hooks";
import { istTime } from "../lib/format";

const KEY_LABEL: Record<string, string> = {
  probability_pct: "probability %", members_exceeding: "members", valid_members: "of", threshold_mm: "threshold mm",
  t_minus_h: "hours to impact", earliest_onset_h: "onset h", within_h: "window h", level: "level", category: "category",
};

export default function Run() {
  const route = useRouteId("run");
  const id = route.id;
  const { data, error, loading, reload, setData } = useApi<any>(id ? `/api/runs/${id}` : null, 0);
  const { events, connected, ended } = useRunTrace(id ?? null);
  const { user } = useApp();
  const readiness = useApi<any>(id ? `/api/runs/${id}/readiness` : null, 0);
  const tasks = useApi<any>(id ? `/api/tasks?run_id=${id}` : null, 0);
  const tasksByAction = useMemo(() => Object.fromEntries((tasks.data?.tasks || []).map((t: any) => [t.action_id, t])), [tasks.data]);

  // refresh the plan when the run finishes (and while it runs, every few seconds)
  useEffect(() => {
    if (ended) { reload(); readiness.reload(); }
  }, [ended, reload]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (data?.run?.status !== "running") return;
    const t = window.setInterval(reload, 4000);
    return () => window.clearInterval(t);
  }, [data?.run?.status, reload]);

  const mapLayers = useMemo(() => {
    const st = data?.state || {};
    return { facilities: st.facilities || [], areas: (st.geocodes || []).filter((g: any) => g.lat != null), routes: st.routes || [] };
  }, [data?.state]);

  if (route.error) return <div className="page"><ErrorBox error={route.error} what="this run" /></div>;
  if (!id || (loading && !data)) return <div className="page"><Loading label="Loading run…" /></div>;
  if (error && !data) return <div className="page"><ErrorBox error={error} what="this run" /></div>;
  if (!data) return null;
  const { run, plan, verification, district, trigger } = data;
  const head = plan?.key_numbers || {};
  const tMinus = data.state?.assessment?.headline?.t_minus_h ?? head.t_minus_h;
  const tStar = trigger?.t_star || data.state?.assessment?.headline?.expected_impact_utc;
  const updateAction = (a: any) => setData({ ...data, actions: data.actions.map((x: any) => (x.id === a.id ? a : x)) });
  const counts = (data.actions || []).reduce((m: any, a: any) => ({ ...m, [a.source]: (m[a.source] || 0) + 1 }), {});

  return (
    <div className="page">
      <div className="hero">
        <div>
          <div className="small muted"><SecureLink kind="district" id={run.district_id}>{district?.name}</SecureLink> / planner run <span className="mono">{run.id}</span></div>
          <h1>Action plan · {district?.name}</h1>
          <div className="small muted">started {istTime(run.started_at, true)} by {run.started_by} · model {run.model || "—"} · {run.tool_calls} tool calls · {run.attempts} plan attempt(s)</div>
        </div>
        {plan?.level && <LevelBadge level={plan.level} big />}
        {tStar && (trigger || (plan?.level && plan.level !== "QUIET")) && <div className="big-cd"><Countdown target={tStar} /><small>TO EXPECTED IMPACT</small></div>}
        <span className={`pill right ${run.status === "completed" ? "ok" : run.status === "running" ? "info" : "warn"}`} style={{ fontSize: 13 }}>{run.status.toUpperCase()}</span>
      </div>

      {run.status === "unavailable" && (
        <div className="banner warn" style={{ marginBottom: 12 }}>
          <AlertTriangle size={16} /> {plan?.gather_note || "Planner unavailable"} · relevant plan sections below
        </div>
      )}
      {run.status === "partial" && <div className="banner danger" style={{ marginBottom: 12 }}><AlertTriangle size={16} /> Partial plan · problems marked on each action</div>}
      {run.status === "failed" && <div className="banner danger" style={{ marginBottom: 12 }}><AlertTriangle size={16} /> Run failed: {run.error}</div>}

      <div className="runlayout">
        <AgentTrace events={events} connected={connected} ended={ended || run.status !== "running"} startedAt={run.started_at} />
        <div className="col" style={{ gap: 14 }}>
          {plan && (
            <div className="card">
              <div className="section-title">Evidence summary</div>
              <div>{plan.evidence_summary}</div>
              {plan.evidence_summary_code && plan.evidence_summary_code !== plan.evidence_summary && (
                <div className="small muted" style={{ marginTop: 6 }}><b>Computed by code:</b> {plan.evidence_summary_code}</div>
              )}
              <div className="row wrap small" style={{ marginTop: 8, gap: 6 }}>
                {Object.entries(head).filter(([, v]: any) => v !== null && v !== "").map(([k, v]: any) => <span key={k} className="pill mono">{KEY_LABEL[k] || k.replace(/_/g, " ")}: {v}</span>)}
              </div>
              <div className="tiny faint" style={{ marginTop: 6 }}>Data {istTime(plan.data_timestamps?.data_time, true)} · run {plan.data_timestamps?.model_run}</div>
            </div>
          )}

          {(data.actions || []).length > 0 && (
            <div className="card">
              <div className="row" style={{ marginBottom: 8 }}>
                <div className="section-title grow" style={{ margin: 0 }}>Actions</div>
                <span className="pill ok">{counts.district_plan || 0} from plan</span>
                <span className="pill info">{counts.generic_guidance || 0} NDMA generic</span>
                <span className="pill warn">{counts.suggested || 0} suggested</span>
                {verification?.passed ? <span className="pill ok"><ShieldCheck size={12} /> verified</span> : verification ? <span className="pill danger">verify: {verification.problems.length} problem(s)</span> : null}
              </div>
              {!user && <div className="banner info small" style={{ marginBottom: 8 }}>Log in to approve actions</div>}
              <ActionTimeline actions={data.actions} tMinus={tMinus} onUpdate={(a) => { updateAction(a); tasks.reload(); }} canAct={!!user}
                readiness={readiness.data?.by_action} tasks={tasksByAction} onReadinessChange={readiness.reload} />
            </div>
          )}

          <div className="grid two">
            {plan && (
              <div className="card">
                <div className="section-title"><CircleHelp size={12} /> Open questions</div>
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{(plan.open_questions || []).map((q: string, i: number) => <li key={i}>{q}</li>)}</ul>
                {!(plan.open_questions || []).length && <div className="small muted">None.</div>}
                <div className="section-title" style={{ marginTop: 12 }}><AlertTriangle size={12} /> Data warnings</div>
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{(plan.warnings || []).map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
                {!(plan.warnings || []).length && <div className="small muted">None.</div>}
              </div>
            )}
            <div className="card">
              <CommsPanel run={data} onReload={reload} />
            </div>
          </div>

          {verification && (
            <div className="card">
              <div className="section-title"><FileCheck2 size={12} /> Verification</div>
              <div className="small">{verification.passed ? "All checks passed" : `${verification.problems.length} problem(s)`}</div>
              <ul className="small">{(verification.problems || []).map((p: any, i: number) => <li key={i}><span className="pill">{p.check}</span> {p.message}</li>)}</ul>
              {plan?.critic && <div className="small muted">Critic: <b>{plan.critic.verdict}</b>{plan.critic.gaps?.length ? ` · gaps: ${plan.critic.gaps.join("; ")}` : ""}</div>}
            </div>
          )}

          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            <div className="row" style={{ padding: "10px 14px" }}>
              <div className="section-title grow" style={{ margin: 0 }}><MapPinned size={12} /> Map</div>
              <span className="tiny muted">{mapLayers.facilities.length} OSM facilities · {mapLayers.areas.length} at-risk areas · {mapLayers.routes.length} safe routes</span>
            </div>
            {district && (
              <MapView districts={[{ id: district.id, name: district.name, hq: district.hq, level: plan?.level ?? null, geometry: district.geometry }]} layers={mapLayers}
                center={[district.hq[1], district.hq[0]]} zoom={9.5} height={380} />
            )}
            {(data.state?.unresolved_places || []).length > 0 && (
              <div className="small muted" style={{ padding: "8px 14px" }}>Not located: {data.state.unresolved_places.join(", ")}</div>
            )}
          </div>

          {run.status === "unavailable" && (data.state?.retrieved_chunks || []).length > 0 && (
            <div className="card">
              <div className="section-title">Relevant plan sections</div>
              {data.state.retrieved_chunks.map((c: any) => (
                <div key={c.chunk_id} className="card tight" style={{ marginBottom: 8, background: "var(--panel2)" }}>
                  <div className="small"><b>{c.document_title}</b> · p. {c.page} {c.section ? `· ${c.section}` : ""}</div>
                  <div className="small" style={{ whiteSpace: "pre-wrap" }}>{c.text.slice(0, 700)}{c.text.length > 700 ? "…" : ""}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
