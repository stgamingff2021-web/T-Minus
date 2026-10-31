import { useEffect, useMemo, useRef, useState } from "react";
import { istFromUnix } from "../lib/format";

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((entries) => setW(Math.max(280, entries[0].contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const lin = (d0: number, d1: number, r0: number, r1: number) => (v: number) => r0 + ((v - d0) / (d1 - d0 || 1)) * (r1 - r0);

function niceMax(v: number) {
  if (v <= 0) return 10;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

function dayTicks(t0: number, t1: number) {
  const out: number[] = [];
  const IST = 19800;
  let t = Math.ceil((t0 + IST) / 86400) * 86400 - IST;
  while (t <= t1) {
    out.push(t);
    t += 86400;
  }
  return out;
}

function istDayLabel(t: number) {
  return new Date(t * 1000).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" });
}

export type Futures = {
  times: number[];
  members: (number | null)[][];
  member_names: string[];
  categories_mm: Record<string, number>;
  now: number;
  model?: string;
  fetched_at?: string;
};

/**
 * Futures chart (§7.2): every ensemble member's 24-hour accumulation as a faint line,
 * IMD thresholds as bold lines, the trigger window and the expected-impact day shaded.
 */
export function FuturesChart({ data, threshold, windowH, tStar, onset }: { data: Futures; threshold: number; windowH?: number; tStar?: number | null; onset?: number | null }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const H = 310;
  const m = { l: 44, r: 16, t: 14, b: 30 };
  const { times, members } = data;
  const t0 = times[0];
  const t1 = times[times.length - 1];
  const maxV = useMemo(() => {
    let mx = 0;
    for (const row of members) for (const v of row) if (v !== null && v > mx) mx = v;
    return niceMax(Math.max(mx, threshold * 1.3, data.categories_mm.extremely_heavy * 0.6));
  }, [members, threshold, data.categories_mm]);
  const x = lin(t0, t1, m.l, width - m.r);
  const y = lin(0, maxV, H - m.b, m.t);
  const crossing = useMemo(() => {
    const end = data.now + (windowH ?? 120) * 3600;
    return members.map((row) => row.some((v, i) => v !== null && v >= threshold && times[i] >= data.now && times[i] <= end));
  }, [members, times, threshold, windowH, data.now]);
  const paths = useMemo(
    () =>
      members.map((row) => {
        let d = "";
        let pen = false;
        row.forEach((v, i) => {
          if (v === null) {
            pen = false;
            return;
          }
          d += `${pen ? "L" : "M"}${x(times[i]).toFixed(1)},${y(v).toFixed(1)}`;
          pen = true;
        });
        return d;
      }),
    [members, times, width, maxV],
  );
  const hoverIdx = hover === null ? null : Math.max(0, Math.min(times.length - 1, Math.round(((hover - m.l) / (width - m.l - m.r)) * (times.length - 1))));
  const hoverStats = useMemo(() => {
    if (hoverIdx === null) return null;
    const vals = members.map((r) => r[hoverIdx]).filter((v): v is number => v !== null).sort((a, b) => a - b);
    const above = vals.filter((v) => v >= threshold).length;
    return { t: times[hoverIdx], above, n: vals.length, median: vals[Math.floor(vals.length / 2)], max: vals[vals.length - 1] };
  }, [hoverIdx, members, threshold, times]);
  const cats = data.categories_mm;
  const ticksY = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxV);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg className="chart" width={width} height={H} onMouseMove={(e) => setHover(e.nativeEvent.offsetX)} onMouseLeave={() => setHover(null)} role="img"
        aria-label={`Futures chart: ${members.length} ensemble members' 24-hour rainfall versus the ${threshold} mm threshold`}>
        <g className="grid">
          {ticksY.map((v) => <line key={v} x1={m.l} x2={width - m.r} y1={y(v)} y2={y(v)} />)}
        </g>
        {windowH && (
          <rect x={x(data.now)} width={Math.max(0, Math.min(x(data.now + windowH * 3600), width - m.r) - x(data.now))} y={m.t} height={H - m.t - m.b} fill="var(--accent)" opacity={0.06} />
        )}
        {tStar && <rect x={x(tStar - 24 * 3600)} width={Math.max(0, x(tStar) - x(tStar - 24 * 3600))} y={m.t} height={H - m.t - m.b} fill="var(--act)" opacity={0.1} />}
        {paths.map((d, i) => (
          <path key={i} d={d} fill="none" stroke={crossing[i] ? "var(--prepare)" : "var(--muted)"} strokeOpacity={crossing[i] ? 0.55 : 0.28} strokeWidth={i === 0 ? 1.6 : 1} />
        ))}
        {(["heavy", "very_heavy", "extremely_heavy"] as const).map((k) =>
          cats[k] !== undefined && cats[k] <= maxV ? (
            <g key={k}>
              <line x1={m.l} x2={width - m.r} y1={y(cats[k])} y2={y(cats[k])} stroke={k === "extremely_heavy" ? "var(--act)" : k === "very_heavy" ? "var(--text)" : "var(--watch)"}
                strokeWidth={cats[k] === threshold ? 2.4 : 1.2} strokeDasharray={cats[k] === threshold ? "" : "5 4"} />
              <text x={width - m.r - 4} y={y(cats[k]) - 4} textAnchor="end" style={{ fill: k === "extremely_heavy" ? "var(--act)" : "var(--text)", fontWeight: 700 }}>
                {k.replace("_", " ")} ≥ {cats[k]} mm
              </text>
            </g>
          ) : null,
        )}
        <line x1={x(data.now)} x2={x(data.now)} y1={m.t} y2={H - m.b} stroke="var(--accent)" strokeWidth={1.5} />
        <text x={x(data.now) + 4} y={m.t + 10} style={{ fill: "var(--accent)", fontWeight: 700 }}>now</text>
        {onset && (
          <g>
            <line x1={x(onset)} x2={x(onset)} y1={m.t} y2={H - m.b} stroke="var(--watch)" strokeDasharray="3 3" />
            <text x={x(onset) + 4} y={m.t + 24} style={{ fill: "var(--watch)" }}>earliest onset</text>
          </g>
        )}
        {tStar && (
          <g>
            <line x1={x(tStar)} x2={x(tStar)} y1={m.t} y2={H - m.b} stroke="var(--act)" strokeWidth={2} />
            <text x={x(tStar) + 4} y={m.t + 38} style={{ fill: "var(--act)", fontWeight: 800 }}>t* expected impact</text>
          </g>
        )}
        <g className="axis">
          {ticksY.map((v) => <text key={v} x={m.l - 6} y={y(v) + 3} textAnchor="end">{Math.round(v)}</text>)}
          <text x={8} y={m.t + 2} transform={`rotate(-90 8 ${m.t + 2})`} textAnchor="end">mm / 24 h</text>
          {dayTicks(t0, t1).map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={H - m.b} y2={H - m.b + 4} />
              <text x={x(t)} y={H - m.b + 16} textAnchor="middle">{istDayLabel(t)}</text>
            </g>
          ))}
        </g>
        {hoverIdx !== null && <line x1={x(times[hoverIdx])} x2={x(times[hoverIdx])} y1={m.t} y2={H - m.b} stroke="var(--text)" strokeOpacity={0.4} />}
      </svg>
      {hoverStats && hover !== null && (
        <div className="tooltip" style={{ left: Math.min(hover + 12, width - 230), top: 18 }}>
          {istFromUnix(hoverStats.t)}<br />
          {hoverStats.above} of {hoverStats.n} members ≥ {threshold} mm<br />
          median {hoverStats.median?.toFixed(1)} · max {hoverStats.max?.toFixed(1)} mm
        </div>
      )}
    </div>
  );
}

/** River discharge vs. the point's own GloFAS reanalysis P95 / P99. */
export function RiverChart({ chart, p95, p99 }: { chart: any; p95?: number; p99?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const H = 190;
  const m = { l: 50, r: 12, t: 10, b: 26 };
  const times: number[] = chart.times || [];
  if (!times.length) return null;
  const med: (number | null)[] = chart.river_discharge_median || chart.river_discharge || [];
  const mx: (number | null)[] = chart.river_discharge_max || [];
  const p25: (number | null)[] = chart.river_discharge_p25 || [];
  const p75: (number | null)[] = chart.river_discharge_p75 || [];
  const all = [...med, ...mx, p99 ?? 0].filter((v): v is number => v !== null && v !== undefined);
  const maxV = niceMax(Math.max(...all, 1) * 1.1);
  const x = lin(times[0], times[times.length - 1], m.l, width - m.r);
  const y = lin(0, maxV, H - m.b, m.t);
  const line = (arr: (number | null)[]) => arr.map((v, i) => (v === null ? "" : `${i === 0 || arr[i - 1] === null ? "M" : "L"}${x(times[i])},${y(v)}`)).join("");
  const band = p25.length && p75.length
    ? `M${times.map((t, i) => `${x(t)},${y(p75[i] ?? 0)}`).join("L")}L${times.slice().reverse().map((t, j) => `${x(t)},${y(p25[times.length - 1 - j] ?? 0)}`).join("L")}Z`
    : "";
  return (
    <div ref={ref}>
      <svg className="chart" width={width} height={H}>
        {band && <path d={band} fill="var(--accent)" opacity={0.15} />}
        <path d={line(mx)} fill="none" stroke="var(--accent)" strokeOpacity={0.5} strokeDasharray="4 3" />
        <path d={line(med)} fill="none" stroke="var(--accent)" strokeWidth={2} />
        {p95 !== undefined && <g><line x1={m.l} x2={width - m.r} y1={y(p95)} y2={y(p95)} stroke="var(--prepare)" strokeWidth={1.5} /><text x={width - m.r - 2} y={y(p95) - 3} textAnchor="end" style={{ fill: "var(--prepare)" }}>P95 {p95.toFixed(0)} m³/s</text></g>}
        {p99 !== undefined && <g><line x1={m.l} x2={width - m.r} y1={y(p99)} y2={y(p99)} stroke="var(--act)" strokeWidth={1.5} /><text x={width - m.r - 2} y={y(p99) - 3} textAnchor="end" style={{ fill: "var(--act)" }}>P99 {p99.toFixed(0)} m³/s</text></g>}
        <g className="axis">
          {[0, 0.5, 1].map((f) => <text key={f} x={m.l - 6} y={y(f * maxV) + 3} textAnchor="end">{Math.round(f * maxV)}</text>)}
          {times.filter((_, i) => i % 5 === 0).map((t) => <text key={t} x={x(t)} y={H - 8} textAnchor="middle">{istDayLabel(t)}</text>)}
        </g>
      </svg>
    </div>
  );
}

const SERIES_COLORS = ["var(--watch)", "var(--prepare)", "var(--act)", "var(--violet)", "var(--accent)"];

/** Replay: multi-model agreement through time, level bands, observed 24-h rain and peak. */
export function ReplayChart({ result }: { result: any }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const H = 300;
  const m = { l: 44, r: 50, t: 14, b: 44 };
  const steps: any[] = result.timeline || [];
  const obs: [number, number | null][] = result.observed?.series || [];
  if (!steps.length) return <div className="empty">No replay steps.</div>;
  const keys = Object.keys(steps[0].agreement || {});
  const tMin = Math.min(steps[0].as_of, obs.length ? obs[0][0] : Infinity);
  const tMax = Math.max(steps[steps.length - 1].as_of, obs.length ? obs[obs.length - 1][0] : -Infinity);
  const x = lin(tMin, tMax, m.l, width - m.r);
  const y = lin(0, 1, H - m.b, m.t);
  const obsMax = niceMax(Math.max(...obs.map((o) => o[1] || 0), result.observed?.peak_mm || 0, 120));
  const y2 = lin(0, obsMax, H - m.b, m.t);
  const lvlColor: Record<string, string> = { WATCH: "var(--watch)", PREPARE: "var(--prepare)", ACT: "var(--act)" };
  const peak = result.observed?.peak_time;
  return (
    <div ref={ref}>
      <svg className="chart" width={width} height={H}>
        <g className="grid">{[0, 0.3, 0.5, 0.7, 1].map((v) => <line key={v} x1={m.l} x2={width - m.r} y1={y(v)} y2={y(v)} strokeDasharray={v === 0.3 || v === 0.5 || v === 0.7 ? "2 4" : ""} />)}</g>
        {obs.length > 1 && (
          <path d={`M${x(obs[0][0])},${y2(0)}${obs.map(([t, v]) => `L${x(t)},${y2(v || 0)}`).join("")}L${x(obs[obs.length - 1][0])},${y2(0)}Z`} fill="var(--accent)" opacity={0.13} />
        )}
        {steps.map((s, i) => s.level && (
          <rect key={i} x={x(s.as_of)} width={Math.max(2, x(steps[Math.min(i + 1, steps.length - 1)].as_of) - x(s.as_of))} y={H - m.b + 4} height={10} fill={lvlColor[s.level]} />
        ))}
        {keys.map((k, ki) => (
          <path key={k} fill="none" stroke={SERIES_COLORS[ki % SERIES_COLORS.length]} strokeWidth={2}
            d={steps.map((s, i) => { const p = s.agreement[k]?.p; return p === null || p === undefined ? "" : `${i === 0 ? "M" : "L"}${x(s.as_of)},${y(p)}`; }).join("")} />
        ))}
        {(["WATCH", "PREPARE", "ACT"] as const).map((lv) => result.first_fire?.[lv] ? (
          <g key={lv}>
            <line x1={x(result.first_fire[lv])} x2={x(result.first_fire[lv])} y1={m.t} y2={H - m.b} stroke={lvlColor[lv]} strokeDasharray="4 3" />
            <text x={x(result.first_fire[lv]) + 3} y={m.t + 10 + ["WATCH", "PREPARE", "ACT"].indexOf(lv) * 12} style={{ fill: lvlColor[lv], fontWeight: 800 }}>{lv} fires</text>
          </g>
        ) : null)}
        {peak && (
          <g>
            <line x1={x(peak)} x2={x(peak)} y1={m.t} y2={H - m.b} stroke="var(--text)" strokeWidth={2} />
            <text x={x(peak) - 4} y={H - m.b - 6} textAnchor="end" style={{ fill: "var(--text)", fontWeight: 800 }}>observed peak {result.observed.peak_mm} mm/24 h</text>
          </g>
        )}
        <g className="axis">
          {[0, 0.5, 1].map((v) => <text key={v} x={m.l - 6} y={y(v) + 3} textAnchor="end">{Math.round(v * 100)}%</text>)}
          {[0, 0.5, 1].map((f) => <text key={f} x={width - m.r + 6} y={y2(f * obsMax) + 3} style={{ fill: "var(--accent)" }}>{Math.round(f * obsMax)}</text>)}
          <text x={width - 8} y={m.t} textAnchor="end" style={{ fill: "var(--accent)" }}>mm</text>
          {dayTicks(tMin, tMax).map((t) => <text key={t} x={x(t)} y={H - 10} textAnchor="middle">{istDayLabel(t)}</text>)}
        </g>
      </svg>
      <div className="row wrap small" style={{ gap: 12 }}>
        {keys.map((k, ki) => <span key={k} className="row" style={{ gap: 4 }}><span className="dot" style={{ background: SERIES_COLORS[ki % SERIES_COLORS.length] }} /> agreement {k}</span>)}
        <span className="row" style={{ gap: 4 }}><span className="dot" style={{ background: "var(--accent)", opacity: 0.4 }} /> observed 24-h rain (reanalysis)</span>
      </div>
    </div>
  );
}

export function Sparkline({ values, width = 90, height = 22, color = "var(--accent)" }: { values: number[]; width?: number; height?: number; color?: string }) {
  if (!values.length) return <span className="faint tiny">—</span>;
  const mx = Math.max(...values, 1);
  const x = lin(0, Math.max(1, values.length - 1), 1, width - 1);
  const y = lin(0, mx, height - 2, 2);
  return (
    <svg width={width} height={height}>
      <path d={values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("")} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}
