import { Megaphone } from "lucide-react";
import { ErrorBox, LevelBadge, Loading } from "../components/ui";
import { useApi } from "../lib/hooks";
import { istTime } from "../lib/format";

export default function Public() {
  const { data, error, loading } = useApi<any>("/api/public/alerts", 20000);
  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="hero"><h1><Megaphone size={20} /> Public alerts</h1></div>
      <div className="banner warn" style={{ marginBottom: 14 }}>
        Exercise · not an official warning. Follow IMD and your district administration.
      </div>
      {loading && <Loading label="Loading approved alerts…" />}
      <ErrorBox error={error} what="public alerts" />
      {data && !data.alerts.length && <div className="card empty">No alerts have been approved for publication.</div>}
      <div className="col" style={{ gap: 12 }}>
        {(data?.alerts || []).map((a: any) => (
          <div key={a.id} className="card">
            <div className="row wrap">
              <LevelBadge level={a.level} />
              <b>{a.district}</b>
              <span className="pill">{a.hazard}</span>
              <span className="pill info">CAP {a.msg_type}</span>
              <span className="tiny muted right">published {istTime(a.published_at, true)} by {a.published_by}</span>
            </div>
            <p style={{ fontSize: 16, margin: "10px 0 6px" }}>{a.message_en}</p>
            <p className="ta" lang="ta" style={{ fontSize: 16, margin: 0 }}>{a.message_ta}</p>
            <div className="tiny muted" style={{ marginTop: 8 }}>
              Data: model run {a.data_timestamps?.model_run || "—"} · fetched {istTime(a.data_timestamps?.data_time, true)} {a.cap_url && <> · <a href={a.cap_url} target="_blank" rel="noreferrer">CAP XML</a></>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
