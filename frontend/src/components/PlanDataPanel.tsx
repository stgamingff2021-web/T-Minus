import { useState } from "react";
import { useApi } from "../lib/hooks";
import { Loading, Modal } from "./ui";

const KINDS: [string, string][] = [["shelter", "Shelters"], ["contact", "Contacts"], ["resource", "Equipment"],
  ["responsibility", "Responsibilities"], ["procedure", "SOPs"], ["asset", "Assets"]];

export function PlanDataPanel({ districtId }: { districtId: string }) {
  const [kind, setKind] = useState("shelter");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const params = new URLSearchParams({ kind, limit: "200" });
  if (query) params.set("q", query);
  const { data, loading } = useApi<any>(`/api/districts/${districtId}/plan-data?${params}`);
  const gaps = useApi<any>(`/api/districts/${districtId}/plan-gaps`);
  const [page, setPage] = useState<{ doc: string; page: number; quote: string } | null>(null);
  return (
    <div className="col" style={{ gap: 12 }}>
      {gaps.data?.gaps?.length > 0 && (
        <div className="card tight">
          <div className="section-title">Plan gaps</div>
          {gaps.data.gaps.map((g: any, i: number) => (
            <div key={i} className="gap"><span className={`sev ${g.severity}`}>{g.severity}</span>
              <span className="grow">{g.message}{g.pages?.length > 0 && <span className="tiny muted"> · p. {g.pages.slice(0, 8).join(", ")}</span>}</span></div>
          ))}
        </div>
      )}
      <div className="card tight">
        <div className="tabs">
          {KINDS.map(([k, label]) => (
            <button key={k} className={`tab ${kind === k ? "active" : ""}`} onClick={() => setKind(k)}>
              {label} <span className="n">{data?.counts?.[k] ?? ""}</span>
            </button>
          ))}
        </div>
        <form className="row" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }} style={{ marginBottom: 6 }}>
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, role, place…" />
          <button className="btn sm">Search</button>
        </form>
        {loading && !data ? <Loading label="Loading…" /> : !data?.documents?.length ? <div className="empty small">No district plan.</div> :
          !data.records.length ? <div className="empty small">Nothing found.</div> : (
            <>
              {data.records.map((r: any) => <Record key={r.id} r={r} onPage={() => setPage({ doc: r.document_id, page: r.page, quote: r.quote })} />)}
              {data.total > data.records.length && <div className="tiny faint center" style={{ padding: 8 }}>{data.records.length} of {data.total} · refine the search</div>}
            </>
          )}
      </div>
      {page && <PageModal {...page} onClose={() => setPage(null)} />}
    </div>
  );
}

function Record({ r, onPage }: { r: any; onPage: () => void }) {
  const f = r.fields || {};
  return (
    <div className="rec">
      <div className="what">
        <b>{r.label}</b>
        <div className="row wrap tiny" style={{ gap: 6, marginTop: 2 }}>
          {f.phones?.map((p: string) => <a key={p} className="mono" href={`tel:${p.replace(/\D/g, "")}`}>{p}</a>)}
          {f.emails?.map((e: string) => <a key={e} href={`mailto:${e}`}>{e}</a>)}
          {f.capacity != null && <span className="chip">capacity {Number(f.capacity).toLocaleString("en-IN")}</span>}
          {f.count != null && <span className="chip">{f.count} sites</span>}
          {f.quantity != null && <span className="chip">{f.quantity} {f.context === "past_use" ? "(past use)" : ""}</span>}
          {f.ward != null && <span className="chip">ward {f.ward}</span>}
          {f.department && <span className="chip">{f.department}</span>}
          {f.asset_type && <span className="chip">{String(f.asset_type).replace("_", " ")}</span>}
          {r.kind === "responsibility" && <span className="muted">{r.quote.slice(0, 220)}</span>}
        </div>
      </div>
      <button className="pagelink" onClick={onPage}>p. {r.page}</button>
    </div>
  );
}

function PageModal({ doc, page, quote, onClose }: { doc: string; page: number; quote: string; onClose: () => void }) {
  const { data } = useApi<any>(`/api/plans/${doc}/pages/${page}`);
  let body: any = data?.text;
  if (data?.text && quote && data.text.includes(quote)) {
    const i = data.text.indexOf(quote);
    body = <>{data.text.slice(0, i)}<mark>{quote}</mark>{data.text.slice(i + quote.length)}</>;
  }
  return (
    <Modal wide title={data ? `${data.title} · p. ${page}` : `p. ${page}`} onClose={onClose}>
      {data ? <div className="pagetext">{body}</div> : <Loading label="Loading page…" />}
    </Modal>
  );
}

