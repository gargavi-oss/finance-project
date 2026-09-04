import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { listAudit, listDocuments, type AuditEntry, type DocumentRecord } from "../lib/api";
import { fmtRelative } from "../lib/format";

export default function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true); setError("");
    try { const [audit, docs] = await Promise.all([listAudit(), listDocuments()]); setEntries(audit); setDocuments(docs); }
    catch { setError("The audit trail could not be loaded."); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);

  const filteredDocs = useMemo(() => documents.filter((doc) => `${doc.vendor} ${doc.filename} ${doc.id} ${doc.decision}`.toLowerCase().includes(query.toLowerCase())), [documents, query]);
  const approved = entries.filter((item) => item.action === "approved").length;
  const escalated = entries.filter((item) => item.action === "escalated").length;
  const rejected = entries.filter((item) => item.action === "rejected").length;

  return (
    <div className="page-stack">
      <section className="page-lead"><div><span className="eyebrow"><span /> Immutable review history</span><h2>Every decision, with its evidence attached.</h2><p>Track submissions, analyst actions, reviewer notes, and completed verdicts from one searchable ledger.</p></div><button className="button button-outline" onClick={load}>Refresh ledger</button></section>
      <div className="summary-strip"><Stat label="Submissions" value={documents.length} /><Stat label="Approved" value={approved} tone="clear" /><Stat label="Escalated" value={escalated} tone="review" /><Stat label="Rejected" value={rejected} tone="critical" /></div>
      {error && <div className="inline-error">{error}</div>}
      <section className="surface audit-surface">
        <div className="surface-heading"><div><span className="section-index">CASE LEDGER</span><h3>All submissions</h3></div><label className="table-search"><SearchIcon /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search vendor or case…" /></label></div>
        {loading ? <TableSkeleton /> : filteredDocs.length ? <div className="responsive-table"><table><thead><tr><th>Case</th><th>Vendor</th><th>Risk</th><th>Decision</th><th>Submitted</th><th /></tr></thead><tbody>{filteredDocs.map((doc) => <tr key={doc.id}><td><code>{doc.id}</code><small>{doc.filename}</small></td><td><strong>{doc.vendor ?? "Processing"}</strong><small>{doc.invoice_number ?? "No invoice number"}</small></td><td><Risk score={doc.risk_score} /></td><td><span className={`status-chip status-${doc.decision}`}>{doc.decision}</span></td><td>{fmtRelative(doc.created_at)}</td><td><Link className="row-link" to={`/app/verdict?id=${doc.id}`}>Open report →</Link></td></tr>)}</tbody></table></div> : <Empty title="No matching submissions" copy="Start a new investigation or adjust your search." />}
      </section>
      <section className="surface audit-surface">
        <div className="surface-heading"><div><span className="section-index">DECISION HISTORY</span><h3>Reviewer actions</h3></div><span>{entries.length} logged events</span></div>
        {loading ? <TableSkeleton /> : entries.length ? <div className="decision-feed">{entries.map((entry) => <article key={entry.id}><span className={`decision-mark ${entry.action}`} /><div><strong>{entry.action}</strong><span>{entry.filename}</span><p>{entry.notes || "No reviewer note was added."}</p></div><div><strong>{entry.actor}</strong><small>{fmtRelative(entry.created_at)}</small><Link to={`/app/verdict?id=${entry.document_id}`}>View evidence</Link></div></article>)}</div> : <Empty title="No decisions recorded" copy="Approve, escalate, or reject a verdict to create the first audit event." />}
      </section>
    </div>
  );
}
function Stat({ label, value, tone = "" }: { label: string; value: number; tone?: string }) { return <div className={tone}><strong>{value}</strong><span>{label}</span></div>; }
function Risk({ score }: { score: number | null }) { if (score === null) return <span className="muted-copy">Processing</span>; const tone = score >= 75 ? "critical" : score >= 50 ? "review" : "clear"; return <span className={`risk-inline ${tone}`}><b>{score}</b><i><em style={{ transform: `scaleX(${score / 100})` }} /></i></span>; }
function Empty({ title, copy }: { title: string; copy: string }) { return <div className="table-empty"><strong>{title}</strong><span>{copy}</span></div>; }
function TableSkeleton() { return <div className="table-skeleton"><span /><span /><span /></div>; }
function SearchIcon(){return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>}
