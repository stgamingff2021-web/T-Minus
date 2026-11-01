import { useState } from "react";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { SecureLink } from "../lib/links";
import { useApi, useNow } from "../lib/hooks";
import { countdown, istTime } from "../lib/format";
import { Loading, Stat } from "./ui";

function dur(hours: number): string {
  const mins = Math.max(1, Math.round(Math.abs(hours) * 60));
  if (mins < 60) return `${mins} min`;
  if (mins < 48 * 60) return `${Math.floor(mins / 60)} h ${mins % 60} m`;
  const h = Math.round(mins / 60);
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

const STATUS = [["pending", "Pending"], ["in_progress", "In progress"], ["completed", "Completed"], ["blocked", "Blocked"]] as const;

export function TasksTable({ districtId, showDistrict = true }: { districtId?: string; showDistrict?: boolean }) {
  const [status, setStatus] = useState("");
  const [dept, setDept] = useState("");
  const q = new URLSearchParams();
  if (districtId) q.set("district_id", districtId);
  if (status) q.set("status", status);
  if (dept) q.set("department", dept);
  const { data, loading, reload } = useApi<any>(`/api/tasks?${q}`, 30000);
  useNow(30000);
  if (loading && !data) return <Loading label="Loading tasks…" />;
  if (!data) return null;
  const c = data.counts;
  const filters: [string, string, number, string?][] = [
    ["", "All", c.pending + c.in_progress + c.completed + c.blocked],
    ["pending", "Pending", c.pending], ["in_progress", "In progress", c.in_progress], ["completed", "Completed", c.completed, "var(--ok)"],
    ["blocked", "Blocked", c.blocked, c.blocked ? "var(--act)" : undefined], ["overdue", "Overdue", c.overdue, c.overdue ? "var(--act)" : undefined],
  ];
  return (
    <div className="col" style={{ gap: 10 }}>
      <div className="row wrap" style={{ justifyContent: "space-between" }}>
        <div className="counts">
          {filters.map(([k, label, n, color]) => (
            <button key={k} onClick={() => setStatus(k)}><div className={`stat ${status === k ? "on" : ""}`}><b style={{ color }}>{n}</b><span>{label}</span></div></button>
          ))}
        </div>
        {data.departments.length > 1 && (
          <select className="input" style={{ width: "auto" }} value={dept} onChange={(e) => setDept(e.target.value)}>
            <option value="">All departments</option>
            {data.departments.map((d: string) => <option key={d}>{d}</option>)}
          </select>
        )}
      </div>
      {!data.tasks.length ? <div className="empty small">{status || dept ? "No tasks match." : "No approved actions yet."}</div> : (
        <div className="scroll">
          <table className="tbl">
            <thead><tr>{showDistrict && <th>District</th>}<th>Action</th><th>Department</th><th>Due</th><th>Status</th><th>Readiness</th></tr></thead>
            <tbody>{data.tasks.map((t: any) => <TaskRow key={t.id} t={t} showDistrict={showDistrict} onSaved={reload} />)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TaskRow({ t, showDistrict, onSaved }: { t: any; showDistrict: boolean; onSaved: () => void }) {
  const { user, setLoginOpen, toast } = useApp();
  const [blocker, setBlocker] = useState(t.blocker || "");
  const [editing, setEditing] = useState(false);
  const save = async (json: any) => {
    if (!user) return setLoginOpen(true);
    try {
      await api(`/api/tasks/${t.id}`, { method: "PATCH", json });
      setEditing(false);
      onSaved();
    } catch (e) {
      toast({ kind: "danger", title: "Not saved", body: (e as Error).message });
    }
  };
  const due = t.due_at ? countdown(t.due_at) : null;
  return (
    <tr className={`${t.overdue ? "overdue" : ""} ${t.status === "blocked" ? "blocked" : ""}`}>
      {showDistrict && <td><SecureLink kind="district" id={t.district_id}>{t.district_name}</SecureLink></td>}
      <td style={{ maxWidth: 420 }}>
        <SecureLink kind="run" id={t.run_id} className="mono tiny">{t.key}</SecureLink> {t.action}
        {t.status === "blocked" && t.blocker && <div className="tiny" style={{ color: "var(--act)" }}>Blocked: {t.blocker}</div>}
      </td>
      <td className="small">{t.department || "—"}</td>
      <td className="small" style={{ whiteSpace: "nowrap" }}>
        {!due ? "—" : t.status === "completed" ? istTime(t.completed_at, true) : <span style={{ color: t.overdue ? "var(--act)" : undefined }} title={istTime(t.due_at, true)}>{due.past ? `${dur(due.hours)} overdue` : `in ${dur(due.hours)}`}</span>}
      </td>
      <td>
        <select className="input" style={{ width: "auto", padding: "2px 6px" }} value={t.status}
          onChange={(e) => (e.target.value === "blocked" ? setEditing(true) : save({ status: e.target.value }))}>
          {STATUS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {editing && (
          <div className="row" style={{ marginTop: 4, gap: 4 }}>
            <input className="input" style={{ padding: "2px 6px" }} value={blocker} onChange={(e) => setBlocker(e.target.value)} placeholder="What is blocking it?" autoFocus />
            <button className="btn xs" disabled={!blocker.trim()} onClick={() => save({ status: "blocked", blocker })}>Save</button>
          </div>
        )}
      </td>
      <td>
        {t.readiness && <span className={`chip ${t.readiness}`}>{t.readiness}</span>}
        {t.readiness_unavailable?.length > 0 && <div className="tiny" style={{ color: "var(--act)" }}>{t.readiness_unavailable.join(", ")}</div>}
      </td>
    </tr>
  );
}

export function TaskSummary({ counts }: { counts: any }) {
  return <div className="row wrap"><Stat value={counts.pending} label="Pending" /><Stat value={counts.blocked} label="Blocked" color="var(--act)" /><Stat value={counts.overdue} label="Overdue" color="var(--act)" /></div>;
}
