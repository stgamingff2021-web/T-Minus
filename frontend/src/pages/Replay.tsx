import { useEffect, useState } from "react";
import { History, Loader2, Play, Target } from "lucide-react";
import { ReplayChart } from "../components/charts";
import { ErrorBox, LevelBadge, Loading } from "../components/ui";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { useApi } from "../lib/hooks";
import { istFromUnix, istTime } from "../lib/format";

export default function Replay() {
  const ev = useApi<any>("/api/replay/events", 0);
  const [replayId, setReplayId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { toast } = useApp();
  const rp = useApi<any>(replayId ? `/api/replay/${replayId}` : null, replayId ? 2500 : 0);

  useEffect(() => {
    if (!replayId && ev.data) {
      const done = ev.data.events.find((e: any) => e.latest?.status === "completed");
      if (done) setReplayId(done.latest.id);
    }
  }, [ev.data, replayId]);

  const start = async (key: string) => {
    setBusy(true);
    try {
      const r = await api("/api/replay", { method: "POST", json: { event_key: key } });
      setReplayId(r.replay_id);
    } catch (e) {
      toast({ kind: "danger", title: "Replay not started", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const res = rp.data?.result;
  return (
    <div className="page full" style={{ height: "auto" }}>
      <div className="banner replay" style={{ borderRadius: 0 }}>
        <History size={16} /> REPLAY · forecasts as issued at the time
      </div>
      <div className="page">
        <div className="hero">
          <div>
            <h1>Replay</h1>
            <div className="small muted">Multi-model agreement · no look-ahead</div>
          </div>
        </div>
        <ErrorBox error={ev.error} what="replay events" />
        <div className="grid three" style={{ marginBottom: 14 }}>
          {(ev.data?.events || []).map((e: any) => (
            <div key={e.key} className="card">
              <b>{e.name}</b>
              <div className="small muted">district: {e.district} · clock {istTime(e.as_of_start, true)} → {istTime(e.as_of_end, true)}</div>
              <div className="tiny muted">quiet period for false alarms: {istTime(e.quiet_start, true)} → {istTime(e.quiet_end, true)}</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn sm primary" onClick={() => start(e.key)} disabled={busy}>{busy ? <Loader2 size={13} className="spin" /> : <Play size={13} />} Run replay</button>
                {e.latest && <button className="btn sm" onClick={() => setReplayId(e.latest.id)}>last: {e.latest.status}</button>}
              </div>
            </div>
          ))}
        </div>
        {replayId && rp.data?.status === "running" && <Loading label="Replaying…" />}
        {rp.data?.status === "failed" && <div className="banner danger">Replay failed: {rp.data.error}</div>}
        {res && (
          <>
            <div className="grid four" style={{ marginBottom: 14 }}>
              {(["WATCH", "PREPARE", "ACT"] as const).map((lv) => (
                <div key={lv} className="card">
                  <div className="row"><LevelBadge level={lv} /><span className="tiny muted right">first fired</span></div>
                  <div className="big-cd" style={{ fontSize: 28, marginTop: 6 }}>{res.lead_time_h[lv] != null ? `${res.lead_time_h[lv]} h` : "—"}<small>BEFORE OBSERVED PEAK</small></div>
                  <div className="tiny muted">{res.first_fire[lv] ? istFromUnix(res.first_fire[lv]) : "did not fire"}</div>
                </div>
              ))}
              <div className="card">
                <div className="row"><Target size={14} /> <b>Target</b></div>
                <div className="small">PREPARE ≥ 24 h ahead of peak rain</div>
                <div style={{ marginTop: 6 }}>{res.target_met ? <span className="pill ok">met</span> : <span className="pill warn">not met</span>}</div>
                <div className="tiny muted" style={{ marginTop: 6 }}>Quiet-period false alarm: {Object.entries(res.quiet_period.false_alarm).filter(([, v]) => v).map(([k]) => k).join(", ") || "none"}</div>
              </div>
            </div>
            <div className="card" style={{ marginBottom: 14 }}>
              <div className="section-title">{res.event.name} · {res.district.name}</div>
              <ReplayChart result={res} />
              {res.observed.error && <div className="banner warn small" style={{ marginTop: 6 }}>Observed rainfall unavailable: {res.observed.error}</div>}
              <div className="small muted" style={{ marginTop: 6 }}>
                Observed peak 24-h rainfall: <b>{res.observed.peak_mm ?? "—"} mm</b> ending {istFromUnix(res.observed.peak_time)} (reanalysis archive). Models used: {res.models_used.join(", ")}.
                {res.models_skipped.length > 0 && <> Skipped: {res.models_skipped.map((m: any) => `${m.model} (${m.reason})`).join("; ")}.</>}
              </div>
            </div>
            <div className="card">
              <div className="section-title">Quiet period (false alarms)</div>
              <ReplayChart result={{ ...res.quiet_period, timeline: res.quiet_period.timeline, observed: res.quiet_period.observed }} />
              <div className="small muted">Observed peak in the quiet period: {res.quiet_period.observed.peak_mm ?? "—"} mm/24 h {res.quiet_period.observed_exceeded_very_heavy ? "(not quiet: very heavy rain observed)" : ""}</div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
