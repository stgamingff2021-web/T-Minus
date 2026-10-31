import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiUrl } from "./api";

/** Fetch JSON with optional polling. Keeps the last good data on refresh errors (never blanks the screen). */
export function useApi<T = any>(path: string | null, intervalMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const pathRef = useRef(path);
  pathRef.current = path;

  const load = useCallback(async () => {
    if (!pathRef.current) return;
    try {
      const d = await api<T>(pathRef.current);
      setData(d);
      setError(null);
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(!!path);
    setData(null);
    if (!path) return;
    load();
    if (intervalMs > 0) {
      const id = window.setInterval(load, intervalMs);
      return () => window.clearInterval(id);
    }
  }, [path, intervalMs, load]);

  return { data, error, loading, reload: load, setData };
}

/** 1-second clock for live countdowns. */
export function useNow(stepMs = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), stepMs);
    return () => window.clearInterval(id);
  }, [stepMs]);
  return now;
}

export type TraceEvent = { seq: number; ts: string; type: string; payload: any };

/** SSE stream of an agent run; EventSource auto-reconnects and resumes with Last-Event-ID. */
export function useRunTrace(runId: string | null) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [ended, setEnded] = useState(false);
  useEffect(() => {
    setEvents([]);
    setEnded(false);
    if (!runId) return;
    const seen = new Set<number>();
    const es = new EventSource(apiUrl(`/api/runs/${runId}/events`));
    es.addEventListener("open", () => setConnected(true));
    es.addEventListener("trace", (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as TraceEvent;
      if (seen.has(ev.seq)) return;
      seen.add(ev.seq);
      setEvents((prev) => [...prev, ev]);
    });
    es.addEventListener("end", () => {
      setEnded(true);
      es.close();
      setConnected(false);
    });
    es.addEventListener("error", () => setConnected(false));
    return () => es.close();
  }, [runId]);
  return { events, connected, ended };
}

export function useLocalStorage<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (nv: T) => {
      setV(nv);
      localStorage.setItem(key, JSON.stringify(nv));
    },
    [key],
  );
  return [v, set];
}
