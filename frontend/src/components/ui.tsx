import { ReactNode, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Eye, HelpCircle, Loader2, Siren, X } from "lucide-react";
import { countdown, Level, levelKey } from "../lib/format";
import { useApp } from "../lib/app";
import { useNow } from "../lib/hooks";

const ICON = { ACT: Siren, PREPARE: AlertTriangle, WATCH: Eye, QUIET: CheckCircle2 } as const;

/** Risk level: colour + icon + text label, always together (§7.1). */
export function LevelBadge({ level, big, noData }: { level: Level; big?: boolean; noData?: boolean }) {
  const { t } = useApp();
  if (noData)
    return (
      <span className={`lvl NODATA ${big ? "big" : ""}`} title="No data">
        <HelpCircle size={big ? 16 : 13} /> NO DATA
      </span>
    );
  const k = levelKey(level);
  const Icon = ICON[k];
  return (
    <span className={`lvl ${k} ${big ? "big" : ""}`} title={k}>
      <Icon size={big ? 16 : 13} strokeWidth={2.6} /> {t(k)}
    </span>
  );
}

export function Countdown({ target, className = "" }: { target?: string | null; className?: string }) {
  const now = useNow(1000);
  const c = countdown(target, now);
  if (!target) return <span className={`mono faint ${className}`}>—</span>;
  return (
    <span className={`mono ${className}`} title={target} style={{ color: c.past ? "var(--act)" : undefined }}>
      {c.text}
    </span>
  );
}

export function Modal({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true">
        <div className="modal-head">
          <div className="grow" style={{ fontWeight: 800 }}>{title}</div>
          <button className="btn ghost sm" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  title, children, confirmLabel = "Confirm", danger, onConfirm, onCancel, busy,
}: { title: string; children: ReactNode; confirmLabel?: string; danger?: boolean; onConfirm: () => void; onCancel: () => void; busy?: boolean }) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className={`btn ${danger ? "danger" : "primary"}`} onClick={onConfirm} disabled={busy}>
            {busy && <Loader2 size={14} className="spin" />} {confirmLabel}
          </button>
        </>
      }
    >
      {children}
    </Modal>
  );
}

export function Toasts() {
  const { toasts, dismiss } = useApp();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <div className="row">
            <b className="grow">{t.title}</b>
            <button className="btn ghost xs" onClick={() => dismiss(t.id)} aria-label="Dismiss"><X size={12} /></button>
          </div>
          {t.body && <div className="small muted">{t.body}</div>}
          {t.href && <a className="small" href={t.href}>Open →</a>}
        </div>
      ))}
    </div>
  );
}

export function Skeleton({ h = 16, w = "100%", label }: { h?: number; w?: number | string; label?: string }) {
  return (
    <div className="col" style={{ gap: 4 }}>
      {label && <span className="tiny faint">{label}</span>}
      <div className="skel" style={{ height: h, width: w }} />
    </div>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="empty row" style={{ justifyContent: "center" }}>
      <Loader2 size={16} className="spin" /> {label}
    </div>
  );
}

export function ErrorBox({ error, what }: { error: Error | null; what: string }) {
  if (!error) return null;
  return (
    <div className="banner danger">
      <AlertTriangle size={16} />
      <div>
        <div>Could not load {what}.</div>
        <div className="small muted">{error.message}</div>
      </div>
    </div>
  );
}

export function LoginModal() {
  const { loginOpen, setLoginOpen, login, t } = useApp();
  const [name, setName] = useState("");
  const [pass, setPass] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!loginOpen) return null;
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await login(name, pass);
      setPass("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={t("login")}
      onClose={() => setLoginOpen(false)}
      footer={
        <>
          <button type="button" className="btn" onClick={() => setLoginOpen(false)} disabled={busy}>Cancel</button>
          <button type="submit" form="login-form" className="btn primary" disabled={busy || !name.trim() || !pass}>
            {busy && <Loader2 size={14} className="spin" />} Sign in
          </button>
        </>
      }
    >
      <form id="login-form" className="col" style={{ gap: 14 }} onSubmit={(e) => { e.preventDefault(); if (!busy && name.trim() && pass) submit(); }}>
        <p className="small muted" style={{ margin: 0 }}>
          Sign in to approve actions, publish exercise alerts and start planner runs. Browsing the dashboard needs no account.
        </p>
        <label className="field">
          Display name
          <input className="input" name="username" autoComplete="username" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name shown in the audit log" maxLength={64} autoFocus />
        </label>
        <label className="field">
          Passcode
          <input className="input" name="password" type="password" autoComplete="current-password" value={pass} onChange={(e) => setPass(e.target.value)} />
        </label>
        {err && <div className="banner danger small" role="alert">{err}</div>}
      </form>
    </Modal>
  );
}

export function SourceBadge({ source }: { source: string }) {
  const label = source === "district_plan" ? "PLAN" : source === "generic_guidance" ? "NDMA GENERIC" : "SUGGESTED — NOT IN DISTRICT PLAN";
  return <span className={`src ${source}`}>{label}</span>;
}

export function Stat({ value, label, color }: { value: ReactNode; label: string; color?: string }) {
  return (
    <div className="stat">
      <b style={{ color }}>{value}</b>
      <span>{label}</span>
    </div>
  );
}
