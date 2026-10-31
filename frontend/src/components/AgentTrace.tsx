import { useEffect, useRef } from "react";
import { AlertOctagon, Brain, CheckCircle2, CircleDot, Cpu, FileCheck2, Flag, PlayCircle, RotateCcw, Wrench } from "lucide-react";
import { TraceEvent } from "../lib/hooks";

const ICONS: Record<string, any> = {
  run: PlayCircle, step: CircleDot, tool_call: Wrench, tool_result: CheckCircle2, retry: RotateCcw,
  error: AlertOctagon, thinking: Brain, verify: FileCheck2, critic: FileCheck2, llm: Cpu, done: Flag,
};

function describe(ev: TraceEvent): string {
  const p = ev.payload || {};
  switch (ev.type) {
    case "run":
      return `Run started · ${p.district} · ${p.hazard} · model ${p.model ?? "none (retrieval-only)"} · budget ${p.budget?.tool_calls} tool calls / ${p.budget?.plan_attempts} plan attempts / ${p.budget?.soft_timeout_s}s`;
    case "step":
      return `${p.node} ${p.status === "start" ? "▶" : "✓"}${p.attempt ? ` (attempt ${p.attempt})` : ""}${p.ms !== undefined ? ` · ${p.ms} ms` : ""}${p.summary ? ` — ${p.summary}` : ""}${p.note ? ` — ${p.note}` : ""}${p.actions !== undefined ? ` — ${p.actions} actions` : ""}${p.passed !== undefined ? ` — ${p.passed ? "hard checks passed" : `${p.problems} problem(s)`}` : ""}${p.mode ? ` — ${p.mode}` : ""}${p.message ? ` — ${p.message}` : ""}`;
    case "tool_call":
      return `${p.tool}(${Object.entries(p.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})${p.by === "code" ? "  [code]" : ""}  #${p.n}`;
    case "tool_result":
      return `→ ${p.summary}  · ${p.ms} ms`;
    case "retry":
      return `RETRY${p.attempt ? ` → attempt ${p.attempt}` : ""} (${p.kind}): ${p.message ?? ""}`;
    case "error":
      return `ERROR ${p.node ?? p.tool ?? ""}: ${p.message}`;
    case "thinking":
      return p.text;
    case "verify":
      return p.passed ? `VERIFY ✓ ${p.stats?.actions} actions · ${p.stats?.citations} citations` : `VERIFY ✗ ${p.problems.length} problem(s):\n${p.problems.map((x: any) => `  • ${x.message}`).join("\n")}`;
    case "critic":
      return `CRITIC: ${p.verdict}${p.issues?.length ? ` — ${p.issues.join("; ")}` : ""}${p.gaps?.length ? ` · gaps: ${p.gaps.join("; ")}` : ""}`;
    case "llm":
      return `${p.model} · ${p.ms} ms · ${p.in}→${p.out} tokens · stop=${p.stop}`;
    case "done":
      return `DONE · ${p.status} · ${p.tool_calls} tool calls · ${p.elapsed_s}s · ${p.tokens_in ?? 0}→${p.tokens_out ?? 0} tokens`;
    default:
      return JSON.stringify(p);
  }
}

export function AgentTrace({ events, connected, ended, startedAt }: { events: TraceEvent[]; connected: boolean; ended: boolean; startedAt?: string }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [events.length]);
  const t0 = startedAt ? Date.parse(startedAt) : events.length ? Date.parse(events[0].ts) : Date.now();
  return (
    <div className="trace" aria-live="polite">
      <div className="trace-head">
        <b className="grow">Live agent trace</b>
        {ended ? <span className="pill ok">finished</span> : connected ? <span className="pill info"><span className="dot live" /> streaming</span> : <span className="pill warn">reconnecting…</span>}
      </div>
      {events.length === 0 && <div className="empty">Waiting for the first step…</div>}
      {events.map((ev) => {
        const Icon = ICONS[ev.type] || CircleDot;
        const bad = ev.type === "tool_result" && ev.payload?.ok === false;
        return (
          <div key={ev.seq} className={`ev ${ev.type} ${bad ? "bad" : ""}`}>
            <span className="t">+{((Date.parse(ev.ts) - t0) / 1000).toFixed(1)}s</span>
            <span className="ico"><Icon size={13} /></span>
            <span className="msg" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{describe(ev)}</span>
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}
