import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, CheckCircle2, CloudOff, Loader2, RefreshCw, Search, ShieldAlert } from "lucide-react";
import { MapView, MapDistrict, LEVEL_COLOR } from "../components/MapView";
import { Countdown, ErrorBox, LevelBadge, Skeleton, Stat } from "../components/ui";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { useApi } from "../lib/hooks";
import { ago, CATEGORY_LABEL, istTime, LEVEL_ORDER, pct } from "../lib/format";

export default function CommandMap() {
  const { data, error, loading, reload } = useApi<any>("/api/districts?geometry=true", 30000);
  const { t, toast } = useApp();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [scanning, setScanning] = useState(false);
  const [watchlist, setWatchlist] = useState(false);

  useEffect(() => {
    const h = () => reload();
    window.addEventListener("tminus:scan", h);
    return () => window.removeEventListener("tminus:scan", h);
  }, [reload]);

  const scan = async () => {
    setScanning(true);
    try {
      const r = await api("/api/scan", { method: "POST" });
      toast({ kind: "info", title: r.status === "already_running" ? "A scan is already running" : "Scan started", body: "Fetching ECMWF ensemble, GloFAS and SACHET…" });
    } catch (e) {
      toast({ kind: "danger", title: "Scan not started", body: (e as Error).message });
    } finally {
      window.setTimeout(() => setScanning(false), 3000);
    }
  };

  const districts: any[] = data?.districts ?? [];
  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return districts.filter(
      (d) => (watchlist ? true : !d.watchlist) && (!ql || d.name.toLowerCase().includes(ql) || d.aliases.some((a: string) => a.toLowerCase().includes(ql))),
    );
  }, [districts, q, watchlist]);

  const mapDistricts: MapDistrict[] = useMemo(
    () =>
      districts.map((d) => ({
        id: d.id, name: d.name, hq: d.hq, level: d.level, geometry: d.geometry,
        noData: !d.rain && !d.level,
        label: d.headline?.probability != null ? `${pct(d.headline.probability)} (${d.headline.n_exceed} of ${d.headline.n_valid}) · ${CATEGORY_LABEL[d.headline.category] ?? ""}` : d.rain?.stale ? "STALE" : "",
      })),
    [districts],
  );
  const onSelect = useCallback((id: string) => nav(`/district/${id}`), [nav]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { ACT: 0, PREPARE: 0, WATCH: 0, QUIET: 0, NODATA: 0 };
    for (const d of districts) {
      if (!d.rain && !d.level) c.NODATA++;
      else c[d.level || "QUIET"]++;
    }
    return c;
  }, [districts]);
  const staleCount = districts.filter((d) => d.rain?.stale).length;
  const disagree = districts.filter((d) => d.disagreement).length;
  const ls = data?.last_scan;

  return (
    <div className="cmd">
      <div className="mapwrap">
        <MapView districts={mapDistricts} onSelect={onSelect} />
        <div className="map-overlay">
          {data?.all_clear && (
            <div className="banner ok">
              <CheckCircle2 size={18} color="var(--ok)" />
              {t("allClear")} — no monitored district above WATCH · last scan {istTime(ls?.finished_at)}
            </div>
          )}
          {!loading && !ls && (
            <div className="banner warn">
              <CloudOff size={18} /> No successful scan yet. Live forecasts appear after the first scan — nothing is shown until real data arrives.
            </div>
          )}
          {ls && ls.status === "failed" && (
            <div className="banner danger">
              <AlertTriangle size={18} />
              <span>Last scan could not reach the live forecast sources ({Object.entries(ls.summary?.errors || {}).filter(([, v]) => v && (typeof v !== "object" || Object.keys(v as any).length)).map(([k]) => k).join(", ") || "see Sources"}). {districts.some((d) => d.rain) ? "Showing the last real readings, marked STALE." : "No value is shown until real data arrives."} <a href="/sources">Sources →</a></span>
            </div>
          )}
          {staleCount > 0 && <div className="banner warn"><AlertTriangle size={18} /> {staleCount} district(s) STALE — their ensemble data could not be refreshed. No new triggers on stale data.</div>}
          {disagree > 0 && <div className="banner warn"><ShieldAlert size={18} /> {t("disagree")} in {disagree} district(s) — the more cautious level is shown.</div>}
        </div>
        <div className="legend small">
          {(["ACT", "PREPARE", "WATCH", "QUIET"] as const).map((l) => <LevelBadge key={l} level={l} />)}
          <span className="lvl NODATA" style={{ borderColor: LEVEL_COLOR.NODATA }}>NO DATA</span>
        </div>
      </div>
      <aside className="side">
        <div className="side-head">
          <div className="row">
            <h2 style={{ margin: 0 }} className="grow">{t("districts")}</h2>
            <button className="btn sm" onClick={scan} disabled={scanning || data?.scan_running}>
              {scanning || data?.scan_running ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} {t("scanNow")}
            </button>
          </div>
          <div className="row wrap" style={{ gap: 6 }}>
            <Stat value={counts.ACT} label="ACT" color="var(--act)" />
            <Stat value={counts.PREPARE} label="PREPARE" color="var(--prepare)" />
            <Stat value={counts.WATCH} label="WATCH" color="var(--watch)" />
            <Stat value={counts.QUIET} label="Quiet" color="var(--ok)" />
            {counts.NODATA > 0 && <Stat value={counts.NODATA} label="No data" color="var(--muted)" />}
          </div>
          <div className="small muted">
            {ls ? <>{t("lastScan")}: <b>{istTime(ls.finished_at, true)}</b> ({ago(ls.finished_at)}) · {ls.summary?.districts_ok ?? 0}/{districts.length} OK · models {(ls.summary?.models_used || []).join(", ") || "—"}</> : "Waiting for the first scan…"}
          </div>
          <div className="row">
            <Search size={14} className="faint" />
            <input className="input" placeholder="Find district (aliases work: Trichy, Tuticorin…)" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <label className="row small muted" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={watchlist} onChange={(e) => setWatchlist(e.target.checked)} /> include watch-list outside Tamil Nadu
          </label>
        </div>
        <ErrorBox error={error} what="districts" />
        <div className="side-list">
          {loading && !data && Array.from({ length: 8 }).map((_, i) => <div key={i} style={{ padding: "10px 14px" }}><Skeleton h={30} /></div>)}
          {shown
            .slice()
            .sort((a, b) => (LEVEL_ORDER[b.level || "QUIET"] - LEVEL_ORDER[a.level || "QUIET"]) || ((a.t_star ? Date.parse(a.t_star) : 9e15) - (b.t_star ? Date.parse(b.t_star) : 9e15)) || a.name.localeCompare(b.name))
            .map((d) => (
              <a key={d.id} className="drow" href={`/district/${d.id}`} onClick={(e) => { e.preventDefault(); nav(`/district/${d.id}`); }}>
                <LevelBadge level={d.level} noData={!d.rain && !d.level} />
                <div style={{ minWidth: 0 }}>
                  <div className="name">{d.name} {d.watchlist && <span className="pill">{d.state}</span>}</div>
                  <div className="sub">
                    {d.headline?.probability != null
                      ? <>{CATEGORY_LABEL[d.headline.category]} · <b>{pct(d.headline.probability)}</b> ({d.headline.n_exceed} of {d.headline.n_valid} {t("forecasts")}) ≤{d.headline.within_h} h</>
                      : d.rain ? "No ensemble member crosses the thresholds" : ls?.status === "failed" ? "Live data unavailable — forecast sources unreachable" : "Awaiting live data"}
                    {d.rain?.stale && <span className="pill warn" style={{ marginLeft: 6 }}>{t("stale")}</span>}
                    {d.rain?.degraded && <span className="pill warn" style={{ marginLeft: 6 }}>{t("degraded")}</span>}
                    {d.disagreement && <span className="pill warn" style={{ marginLeft: 6 }}>{t("disagree")}</span>}
                    {d.stand_down_recommended && <span className="pill info" style={{ marginLeft: 6 }}>stand-down recommended</span>}
                    {d.official?.length > 0 && <span className="pill danger" style={{ marginLeft: 6 }}>{d.official[0].color || "official"} alert</span>}
                  </div>
                </div>
                <div className="cd">{d.level && d.t_star ? <Countdown target={d.t_star} /> : <span className="faint">—</span>}</div>
              </a>
            ))}
          {data && shown.length === 0 && <div className="empty">No district matches “{q}”.</div>}
        </div>
      </aside>
    </div>
  );
}
