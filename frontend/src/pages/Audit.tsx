import { ScrollText } from "lucide-react";
import { ErrorBox, Loading } from "../components/ui";
import { useApp } from "../lib/app";
import { useApi } from "../lib/hooks";
import { istTime } from "../lib/format";

export default function Audit() {
  const { user } = useApp();
  const { data, error, loading } = useApi<any>(user ? "/api/audit" : null, 15000);
  return (
    <div className="page">
      <div className="hero">
        <div>
          <h1><ScrollText size={20} /> Audit log</h1>
          <div className="small muted">Append-only: who, what, when, text before and after. The database blocks updates and deletes on this table.</div>
        </div>
      </div>
      {!user && <div className="banner info">Officer login required.</div>}
      {loading && user && <Loading label="Loading audit log…" />}
      <ErrorBox error={error} what="the audit log" />
      {data && (
        <div className="card scroll">
          <table className="t small">
            <thead><tr><th>Time</th><th>Officer</th><th>Decision</th><th>Target</th><th>Before</th><th>After</th><th>Note</th></tr></thead>
            <tbody>
              {data.entries.map((e: any) => (
                <tr key={e.id}>
                  <td className="mono">{istTime(e.ts, true)}</td>
                  <td>{e.user} <span className="pill">{e.role}</span></td>
                  <td><b>{e.decision}</b></td>
                  <td className="mono tiny">{e.target_type}<br />{e.target_id}</td>
                  <td className="tiny" style={{ maxWidth: 260, whiteSpace: "pre-wrap" }}>{(e.before || "").slice(0, 300)}</td>
                  <td className="tiny" style={{ maxWidth: 260, whiteSpace: "pre-wrap" }}>{(e.after || "").slice(0, 300)}</td>
                  <td className="tiny">{e.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data.entries.length && <div className="empty small">No decisions recorded yet.</div>}
        </div>
      )}
    </div>
  );
}
