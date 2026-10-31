import { useState } from "react";
import { BookOpenText, Loader2, MessageSquareQuote, Upload } from "lucide-react";
import { ErrorBox, Loading } from "../components/ui";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { useApi } from "../lib/hooks";
import { istTime } from "../lib/format";

export default function Plans() {
  const { data, error, loading, reload } = useApi<any>("/api/plans", 0);
  const districts = useApi<any>("/api/districts", 0);
  const { requireLogin, toast } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("district");
  const [district, setDistrict] = useState("chennai");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [year, setYear] = useState("");
  const [busy, setBusy] = useState(false);
  const [qDistrict, setQDistrict] = useState("chennai");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<any>(null);
  const [asking, setAsking] = useState(false);

  const upload = async () => {
    if (!requireLogin()) return;
    if (!file) return toast({ kind: "warn", title: "Choose a PDF first" });
    if (file.size > 30 * 1024 * 1024) return toast({ kind: "danger", title: "File too large", body: "Limit is 30 MB." });
    const fd = new FormData();
    fd.append("file", file);
    fd.append("kind", kind);
    if (kind !== "generic") fd.append("district_id", district);
    fd.append("title", title || file.name.replace(/\.pdf$/i, ""));
    if (url) fd.append("source_url", url);
    if (year) fd.append("edition_year", year);
    setBusy(true);
    try {
      const r = await api("/api/plans", { method: "POST", body: fd });
      toast({ kind: "ok", title: r.duplicate ? "Already ingested (same SHA-256)" : `Ingested ${r.pages} pages → ${r.chunks} chunks`, body: r.ocr_pages?.length ? `OCR on pages ${r.ocr_pages.join(", ")}` : undefined });
      setFile(null);
      reload();
    } catch (e) {
      toast({ kind: "danger", title: "Upload rejected", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const ask = async () => {
    if (!requireLogin()) return;
    setAsking(true);
    setAnswer(null);
    try {
      setAnswer(await api("/api/ask", { method: "POST", json: { district_id: qDistrict, question } }));
    } catch (e) {
      toast({ kind: "danger", title: "Question not answered", body: (e as Error).message });
    } finally {
      setAsking(false);
    }
  };
  const dlist = (districts.data?.districts || []).slice().sort((a: any, b: any) => a.name.localeCompare(b.name));

  return (
    <div className="page">
      <div className="hero">
        <div>
          <h1><BookOpenText size={20} /> Plans</h1>
        </div>
      </div>
      <div className="grid two" style={{ marginBottom: 14 }}>
        <div className="card">
          <div className="section-title"><Upload size={12} /> Add a plan (PDF)</div>
          <div className="col">
            <input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <div className="grid two">
              <label className="field">Kind<select value={kind} onChange={(e) => setKind(e.target.value)}><option value="district">District DDMP</option><option value="city">City plan</option><option value="generic">NDMA generic guideline</option></select></label>
              {kind !== "generic" && <label className="field">District<select value={district} onChange={(e) => setDistrict(e.target.value)}>{dlist.map((d: any) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>}
            </div>
            <label className="field">Title<input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Chennai District Disaster Management Plan 2024" /></label>
            <div className="grid two">
              <label className="field">Official source URL<input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://tnsdma.tn.gov.in/…" /></label>
              <label className="field">Edition year<input className="input" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="2024" /></label>
            </div>
            <button className="btn primary" onClick={upload} disabled={busy}>{busy ? <Loader2 size={14} className="spin" /> : <Upload size={14} />} Ingest</button>
            <div className="tiny muted">PDF · max 30 MB</div>
          </div>
        </div>
        <div className="card">
          <div className="section-title"><MessageSquareQuote size={12} /> Ask the plan</div>
          <div className="col">
            <label className="field">District<select value={qDistrict} onChange={(e) => setQDistrict(e.target.value)}>{dlist.map((d: any) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
            <label className="field">Question<input className="input" value={question} maxLength={500} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && question.trim() && ask()} placeholder="Which department deploys dewatering pumps?" /></label>
            <button className="btn" onClick={ask} disabled={asking || question.trim().length < 4}>{asking && <Loader2 size={14} className="spin" />} Ask</button>
            {answer && (
              <div className="card tight" style={{ background: "var(--panel2)" }}>
                {answer.llm_unavailable ? <div className="small muted">Most relevant passages:</div> : <div>{answer.answer}</div>}
                {(answer.citations || []).map((c: any, i: number) => (
                  <div key={i} className="small" style={{ marginTop: 6 }}>
                    <span className={`pill ${c.verified ? "ok" : "danger"}`}>{c.verified ? "verbatim ✓" : "unverified"}</span> “{c.quote}” — {c.document_title}, p. {c.page}
                  </div>
                ))}
                {(answer.chunks || []).map((c: any) => <div key={c.chunk_id} className="small" style={{ marginTop: 6 }}><b>{c.document_title}</b> p. {c.page}: {c.text.slice(0, 300)}…</div>)}
              </div>
            )}
          </div>
        </div>
      </div>
      <div className="card">
        <div className="section-title">Ingested documents</div>
        {loading && <Loading label="Loading documents…" />}
        <ErrorBox error={error} what="documents" />
        <table className="t small">
          <thead><tr><th>Document</th><th>Scope</th><th>Pages / chunks</th><th>SHA-256</th><th>Ingested</th><th>OCR</th></tr></thead>
          <tbody>
            {(data?.documents || []).map((d: any) => (
              <tr key={d.id}>
                <td><b>{d.title}</b> {d.edition_year ? `(${d.edition_year})` : ""}<div className="tiny">{d.source_url ? <a href={d.source_url} target="_blank" rel="noreferrer">{d.source_url}</a> : <span className="faint">no source URL</span>}</div></td>
                <td>{d.kind === "generic" ? <span className="pill info">NDMA generic</span> : <>{d.district_id} <span className="pill">{d.kind}</span></>}</td>
                <td className="mono">{d.pages} / {d.chunks}</td>
                <td className="mono tiny">{d.sha256.slice(0, 16)}…</td>
                <td className="tiny">{istTime(d.ingested_at, true)}<div className="muted">{d.ingested_by}</div></td>
                <td className="tiny">{d.ocr_pages?.length ? `${d.ocr_pages.length} pages${d.low_confidence_pages?.length ? `, review p. ${d.low_confidence_pages.join(", ")}` : ""}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && !data.documents.length && <div className="empty small">No plans yet</div>}
        <div className="divider" />
        <div className="section-title">Official sources</div>
        <ul className="small">
          {(data?.configured_sources || []).map((p: any) => <li key={p.key}>{p.title} — {p.source_url ? <a href={p.source_url}>{p.source_url}</a> : <>PDF link to be copied from <a href={p.landing_page} target="_blank" rel="noreferrer">{p.landing_page}</a></>}</li>)}
        </ul>
      </div>
    </div>
  );
}
