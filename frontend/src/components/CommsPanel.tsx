import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Download, FileCode2, Loader2, Megaphone, ShieldCheck, XCircle } from "lucide-react";
import { api, apiUrl } from "../lib/api";
import { useApp } from "../lib/app";
import { ConfirmDialog } from "./ui";

// Mirror of the backend's SMS counting (backend re-validates on save and on publish).
const GSM7 = new Set(
  "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà".split(""),
);
const GSM7_EXT = new Set("^{}\\[~]|€\f".split(""));

export function smsCount(text: string) {
  const chars = Array.from(text);
  const gsm = chars.every((c) => GSM7.has(c) || GSM7_EXT.has(c));
  if (gsm) {
    const units = chars.reduce((n, c) => n + (GSM7_EXT.has(c) ? 2 : 1), 0);
    return { encoding: "GSM-7", units, segments: units === 0 ? 0 : units <= 160 ? 1 : Math.ceil(units / 153) };
  }
  let units = 0;
  for (const c of chars) units += c.codePointAt(0)! > 0xffff ? 2 : 1;
  return { encoding: "UCS-2", units, segments: units === 0 ? 0 : units <= 70 ? 1 : Math.ceil(units / 67) };
}

function MessageEditor({ comm, limit, label, lang, onSaved, canAct }: { comm: any; limit: number; label: string; lang: "en" | "ta"; onSaved: (c: any) => void; canAct: boolean }) {
  const [text, setText] = useState(comm.content);
  const [saving, setSaving] = useState(false);
  const { requireLogin, toast } = useApp();
  useEffect(() => setText(comm.content), [comm.content]);
  const c = smsCount(text);
  const over = c.units > limit || (lang === "en" && c.encoding !== "GSM-7");
  const dirty = text !== comm.content;
  const save = async () => {
    if (!requireLogin()) return;
    setSaving(true);
    try {
      onSaved(await api(`/api/communications/${comm.id}`, { method: "PATCH", json: { content: text } }));
    } catch (e) {
      toast({ kind: "danger", title: "Not saved", body: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="col" style={{ gap: 4 }}>
      <div className="row">
        <b className="grow">{label}</b>
        <span className={`counter ${over ? "bad" : "good"}`}>
          {c.units}/{limit} chars · {c.encoding} · {c.segments} SMS segment{c.segments === 1 ? "" : "s"}
        </span>
      </div>
      <textarea className={lang === "ta" ? "ta" : ""} value={text} onChange={(e) => setText(e.target.value)} disabled={!canAct || comm.status === "published"} rows={3} lang={lang} />
      {over && <div className="tiny" style={{ color: "var(--act)" }}>{lang === "en" && c.encoding !== "GSM-7" ? "Non-GSM characters force UCS-2 (70 chars). " : ""}Over the limit — publishing is blocked until fixed.</div>}
      {(comm.validation?.problems || []).length > 0 && !dirty && <div className="tiny" style={{ color: "var(--act)" }}>{comm.validation.problems.join(" · ")}</div>}
      {dirty && canAct && <button className="btn sm" onClick={save} disabled={saving}>{saving && <Loader2 size={12} className="spin" />} Save edit (re-validates)</button>}
    </div>
  );
}

export function CommsPanel({ run, onReload }: { run: any; onReload: () => void }) {
  const { user, requireLogin, toast } = useApp();
  const comms: any[] = run.communications || [];
  const by = useMemo(() => Object.fromEntries(comms.map((c) => [c.kind, c])), [comms]);
  const [confirm, setConfirm] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [showXml, setShowXml] = useState(false);
  const canAct = !!user;
  const published = by.public_en?.status === "published";

  const openConfirm = async () => {
    if (!requireLogin()) return;
    setBusy(true);
    try {
      setConfirm(await api(`/api/communications/${by.cap.id}/confirmation`, { method: "POST" }));
    } catch (e) {
      toast({ kind: "danger", title: "Cannot publish yet", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const publish = async () => {
    setBusy(true);
    try {
      const r = await api(`/api/communications/${by.cap.id}/approve`, { method: "POST", json: { confirmation_token: confirm.token } });
      toast({ kind: "ok", title: `Published (exercise) · CAP ${r.msg_type}`, body: "Visible on the public alerts page.", href: "/public" });
      setConfirm(null);
      onReload();
    } catch (e) {
      toast({ kind: "danger", title: "Publish blocked", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row">
        <div className="section-title grow" style={{ margin: 0 }}><Megaphone size={12} /> Communications (drafts — nothing is sent)</div>
        <a className="btn sm" href={apiUrl(`/api/runs/${run.run.id}/briefing.pdf`)} target="_blank" rel="noreferrer"><Download size={13} /> Briefing PDF</a>
        <a className="btn sm ghost" href={apiUrl(`/api/runs/${run.run.id}/briefing.html`)} target="_blank" rel="noreferrer">HTML</a>
      </div>
      {!by.public_en && (
        <div className="banner info small">{run.plan?.no_public_message_reason || (run.run.status === "running" ? "Drafting…" : "No public message")}</div>
      )}
      {by.public_en && (
        <>
          <MessageEditor comm={by.public_en} limit={160} label="Public message · English (1 SMS)" lang="en" onSaved={onReload} canAct={canAct} />
          <MessageEditor comm={by.public_ta} limit={134} label="பொது செய்தி · Tamil (2 Unicode SMS)" lang="ta" onSaved={onReload} canAct={canAct} />
          {by.cap && (
            <div className="col" style={{ gap: 4 }}>
              <div className="row">
                <b className="grow"><FileCode2 size={13} /> CAP 1.2 alert</b>
                {by.cap.valid ? <span className="pill ok"><ShieldCheck size={12} /> XSD valid</span> : <span className="pill danger"><XCircle size={12} /> XSD invalid</span>}
                <span className="pill violet">status = Exercise</span>
                <button className="btn xs" onClick={() => setShowXml(!showXml)}>{showXml ? "hide" : "view"} XML</button>
                <a className="btn xs ghost" href={apiUrl(`/api/communications/${by.cap.id}/cap.xml`)} target="_blank" rel="noreferrer">raw</a>
              </div>
              {(by.cap.validation?.errors || []).length > 0 && <div className="tiny" style={{ color: "var(--act)" }}>{by.cap.validation.errors.join(" · ")}</div>}
              {showXml && <pre className="xml">{by.cap.content}</pre>}
            </div>
          )}
          <div className="row">
            {published ? (
              <span className="banner ok small grow"><CheckCircle2 size={14} /> Published (exercise) {run.published?.[0] ? `· ${run.published[0].msg_type}` : ""}</span>
            ) : (
              <button className="btn primary grow" onClick={openConfirm} disabled={busy || !by.cap} style={{ justifyContent: "center" }}>
                {busy ? <Loader2 size={14} className="spin" /> : <Megaphone size={14} />} Approve &amp; publish…
              </button>
            )}
          </div>
        </>
      )}
      {confirm && (
        <ConfirmDialog title="Approve & publish these messages?" confirmLabel="Approve & publish (exercise)" busy={busy} onCancel={() => setConfirm(null)} onConfirm={publish}>
          <div className="banner warn small">{confirm.notice}</div>
          <div className="card tight" style={{ background: "var(--panel2)" }}><div className="tiny faint">English</div>{confirm.will_publish.public_en}</div>
          <div className="card tight ta" style={{ background: "var(--panel2)" }}><div className="tiny faint">Tamil</div>{confirm.will_publish.public_ta}</div>
          {!confirm.valid && <div className="banner danger small">Fix first: {confirm.problems.join(" · ")}</div>}
        </ConfirmDialog>
      )}
    </div>
  );
}
