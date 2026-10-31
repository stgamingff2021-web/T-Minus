// Formatting helpers. All times are shown in IST (the officers' clock) with the zone label.

export type Level = "WATCH" | "PREPARE" | "ACT" | "QUIET" | null | undefined;

const IST = "Asia/Kolkata";

export function istTime(iso?: string | null, withDate = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const opts: Intl.DateTimeFormatOptions = { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false };
  if (withDate) Object.assign(opts, { day: "2-digit", month: "short" });
  return `${d.toLocaleString("en-IN", opts)} IST`;
}

export function istFromUnix(ts?: number | null, withDate = true): string {
  return ts ? istTime(new Date(ts * 1000).toISOString(), withDate) : "—";
}

export function ago(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "just now";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function pct(p?: number | null, digits = 1): string {
  return p === null || p === undefined ? "—" : `${(p * 100).toFixed(digits)}%`;
}

export function countdown(targetIso?: string | null, now = Date.now()): { text: string; past: boolean; hours: number } {
  if (!targetIso) return { text: "—", past: false, hours: NaN };
  const ms = new Date(targetIso).getTime() - now;
  const past = ms < 0;
  const abs = Math.abs(ms);
  const h = Math.floor(abs / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  const s = Math.floor((abs % 60_000) / 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return { text: `${past ? "T+" : "T−"}${pad(h)}:${pad(m)}:${pad(s)}`, past, hours: ms / 3_600_000 };
}

export const LEVEL_ORDER: Record<string, number> = { ACT: 3, PREPARE: 2, WATCH: 1, QUIET: 0 };

export function levelKey(l: Level): "ACT" | "PREPARE" | "WATCH" | "QUIET" {
  return (l && l !== "QUIET" ? l : "QUIET") as any;
}

export const CATEGORY_LABEL: Record<string, string> = {
  heavy: "Heavy rain",
  very_heavy: "Very heavy rain",
  extremely_heavy: "Extremely heavy rain",
};

export function num(x?: number | null, digits = 1): string {
  return x === null || x === undefined || Number.isNaN(x) ? "—" : x.toFixed(digits);
}
