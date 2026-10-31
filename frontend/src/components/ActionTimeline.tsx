import { useState } from "react";
import { Check, FileText, Pencil, RotateCcw, X } from "lucide-react";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { useApi } from "../lib/hooks";
import { ReadinessChips } from "./Readiness";
import { ConfirmDialog, Modal, SourceBadge } from "./ui";

const PHASES = ["Do now", "T-48h", "T-24h", "T-6h"] as const;
const PHASE_H: Record<string, number> = { "T-48h": 48, "T-24h": 24, "T-6h": 6 };

function Highlight({ text, quote }: { text: string; quote: string }) {
  const norm = (s: string) => s.replace(/\s+/g, " ");
  const t = norm(text);
  const q = norm(quote).trim();
  const i = q ? t.indexOf(q) : -1;
  if (i < 0) return <>{t}</>;
  return (
    <>
      {t.slice(0, i)}
      <mark>{t.slice(i, i + q.length)}</mark>
      {t.slice(i + q.length)}
    </>
  );
}

export function CitationModal({ cite, onClose }: { cite: any; onClose: () => void }) {
  const { data: chunk, error } = useApi<any>(`/api/plans/chunks/${encodeURIComponent(cite.chunk_id)}`);
  const err = error?.message;
  return (
    <Modal title={<>📄 {cite.document_title} — page {cite.page}</>} onClose={onClose} wide>
      <div className="small muted">
        {cite.section && <>Section: <b>{cite.section}</b> · </>}chunk <span className="mono">{cite.chunk_id}</span>
        {cite.source_url && <> · <a href={cite.source_url} target="_blank" rel="noreferrer">official source</a></>}
        {cite.kind === "generic" && <span className="pill info" style={{ marginLeft: 6 }}>NDMA generic</span>}
      </div>
      <blockquote style={{ margin: 0, padding: "10px 14px", borderLeft: "3px solid var(--watch)", background: "var(--panel2)", borderRadius: 8 }}>
        “{cite.quote}”
      </blockquote>
      {err && <div className="banner danger small">{err}</div>}
      {chunk && (
        <div className="small" style={{ whiteSpace: "pre-wrap", lineHeight: 1.6, maxHeight: 360, overflow: "auto", background: "var(--bg2)", padding: 12, borderRadius: 10, border: "1px solid var(--border)" }}>
          <Highlight text={chunk.text} quote={cite.quote} />
        </div>
      )}
    </Modal>
  );
}

type Extra = { readiness?: Record<string, any>; tasks?: Record<string, any>; onReadinessChange?: () => void };
const TASK_LABEL: Record<string, string> = { pending: "Pending", in_progress: "In progress", completed: "Completed", blocked: "Blocked" };

function ActionCard({ a, onChange, canAct, readiness, tasks, onReadinessChange }: { a: any; onChange: (x: any) => void; canAct: boolean } & Extra) {
  const { requireLogin, toast, t } = useApp();
  const [cite, setCite] = useState<any>(null);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(a.edited_text || a.action);
  const [confirm, setConfirm] = useState<null | "approve" | "reject">(null);
  const [busy, setBusy] = useState(false);
  const send = async (decision: string, extra: any = {}) => {
    if (!requireLogin()) return;
    setBusy(true);
    try {
      const r = await api(`/api/actions/${a.id}`, { method: "PATCH", json: { decision, ...extra } });
      onChange(r);
      setEditing(false);
      setConfirm(null);
    } catch (e) {
      toast({ kind: "danger", title: "Not saved", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`acard ${a.status} ${a.problems?.length ? "problem" : ""}`}>
      <div className="row wrap" style={{ gap: 6 }}>
        <span className="mono tiny faint">{a.key}</span>
        <SourceBadge source={a.source} />
        {a.status !== "proposed" && <span className={`pill ${a.status === "approved" ? "ok" : a.status === "rejected" ? "danger" : "info"}`}>{a.status}</span>}
        {tasks?.[a.id] && <span className={`pill ${tasks[a.id].status === "blocked" || tasks[a.id].overdue ? "danger" : tasks[a.id].status === "completed" ? "ok" : ""}`}>
          {TASK_LABEL[tasks[a.id].status]}{tasks[a.id].overdue ? " · overdue" : ""}</span>}
      </div>
      {editing ? (
        <>
          <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} />
          <div className="row">
            <button className="btn sm primary" disabled={busy} onClick={() => send("edit", { text })}>Save edit</button>
            <button className="btn sm" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </>
      ) : (
        <div className="atext">{a.edited_text || a.action}{a.edited_text && <div className="tiny faint" style={{ textDecoration: "line-through" }}>{a.action}</div>}</div>
      )}
      <div className="resp">{a.responsible ? <>👤 {a.responsible}</> : <span className="faint">No owner in plan</span>}</div>
      {a.citations?.length > 0 && (
        <div className="row wrap" style={{ gap: 4 }}>
          {a.citations.map((c: any, i: number) => (
            <span key={i} className="cite" onClick={() => setCite(c)} title={c.quote}>
              <FileText size={11} /> {c.document_title ? `${c.document_title.slice(0, 26)}${c.document_title.length > 26 ? "…" : ""}` : "?"} · p.{c.page ?? "?"}
            </span>
          ))}
        </div>
      )}
      {a.rationale && <div className="tiny muted">{a.rationale}</div>}
      {a.problems?.length > 0 && <div className="tiny" style={{ color: "var(--act)" }}>⚠ {a.problems.join(" · ")}</div>}
      <ReadinessChips checks={readiness?.[a.id]?.checks} onChange={onReadinessChange} />
      {a.location_refs?.length > 0 && <div className="tiny faint">📍 {a.location_refs.join(", ")}</div>}
      {canAct && !editing && (
        <div className="row" style={{ gap: 4 }}>
          <button className="btn xs ok" onClick={() => setConfirm("approve")} disabled={busy}><Check size={12} /> {t("approve")}</button>
          <button className="btn xs" onClick={() => (requireLogin() ? setEditing(true) : null)} disabled={busy}><Pencil size={12} /> {t("edit")}</button>
          <button className="btn xs danger" onClick={() => setConfirm("reject")} disabled={busy}><X size={12} /> {t("reject")}</button>
          {a.status !== "proposed" && <button className="btn xs ghost" onClick={() => send("reset")} title="Back to proposed"><RotateCcw size={12} /></button>}
        </div>
      )}
      {cite && <CitationModal cite={cite} onClose={() => setCite(null)} />}
      {confirm && (
        <ConfirmDialog title={confirm === "approve" ? "Approve this action?" : "Reject this action?"} confirmLabel={confirm === "approve" ? "Approve" : "Reject"}
          danger={confirm === "reject"} busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => send(confirm, { confirm: true })}>
          <div className="card tight" style={{ background: "var(--panel2)" }}>{a.edited_text || a.action}</div>
        </ConfirmDialog>
      )}
    </div>
  );
}

export function ActionTimeline({ actions, tMinus, onUpdate, canAct, ...extra }: { actions: any[]; tMinus?: number | null; onUpdate: (a: any) => void; canAct: boolean } & Extra) {
  return (
    <div className="timeline">
      {PHASES.map((ph) => {
        const passed = ph !== "Do now" && tMinus != null && tMinus <= PHASE_H[ph];
        const items = actions.filter((a) => a.phase === ph);
        return (
          <div key={ph} className={`phase ${passed && !items.length ? "disabled" : ""}`}>
            <div className="phase-head">
              <span>{ph === "Do now" ? "▶ DO NOW" : ph}</span>
              <span className="tiny faint">{passed ? "passed → Do now" : `${items.length}`}</span>
            </div>
            {items.map((a) => <ActionCard key={a.id} a={a} onChange={onUpdate} canAct={canAct} {...extra} />)}
            {!items.length && <div className="tiny faint center" style={{ padding: 10 }}>{passed ? "Passed" : "No actions"}</div>}
          </div>
        );
      })}
    </div>
  );
}
