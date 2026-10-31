import { useState } from "react";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { Modal } from "./ui";

export type Check = { id: number; requirement: string; label: string; status: "ready" | "unavailable" | "unverified"; reason: string; evidence: any[]; verified_by?: string | null };
const WORD = { ready: "Ready", unavailable: "Unavailable", unverified: "Unverified" };

export function ReadinessChips({ checks, onChange }: { checks?: Check[]; onChange?: () => void }) {
  const [open, setOpen] = useState<Check | null>(null);
  if (!checks?.length) return null;
  return (
    <div className="row wrap" style={{ gap: 4, marginTop: 6 }}>
      {checks.map((c) => (
        <button key={c.id} className={`chip ${c.status}`} onClick={() => setOpen(c)} title={c.reason}>
          {c.label}: {WORD[c.status]}{c.verified_by ? " ✓" : ""}
        </button>
      ))}
      {open && <CheckModal check={open} onClose={() => setOpen(null)} onChange={onChange} />}
    </div>
  );
}

function CheckModal({ check, onClose, onChange }: { check: Check; onClose: () => void; onChange?: () => void }) {
  const { user, setLoginOpen, toast } = useApp();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const mark = async (status: Check["status"]) => {
    if (!user) return setLoginOpen(true);
    setBusy(true);
    try {
      await api(`/api/readiness/${check.id}`, { method: "PATCH", json: { status, note: note || undefined } });
      onChange?.();
      onClose();
    } catch (e) {
      toast({ kind: "danger", title: "Not saved", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`${check.label} · ${WORD[check.status]}`} onClose={onClose}
      footer={<>
        <button className="btn sm ok" disabled={busy} onClick={() => mark("ready")}>Confirm ready</button>
        <button className="btn sm danger" disabled={busy} onClick={() => mark("unavailable")}>Mark unavailable</button>
      </>}>
      <div>{check.reason}</div>
      {check.evidence?.map((e, i) => (
        <div key={i} className="tiny muted" style={{ marginTop: 6 }}>
          {e.source === "plan" ? `Plan p.${e.page}: “${(e.quote || "").slice(0, 160)}”` : e.source === "osm" ? `OpenStreetMap: ${e.label || e.osm_id}` : e.label}
        </div>
      ))}
      <label className="field" style={{ marginTop: 10 }}>Note<input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. checked with Tahsildar, 3 boats ready" /></label>
    </Modal>
  );
}
