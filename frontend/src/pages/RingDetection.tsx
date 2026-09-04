import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { listRings } from "../lib/api";
import { fmtRelative } from "../lib/format";

interface RingEntry { id: string; filename: string; vendor: string | null; created_at: string; perceptual_hash: string; }
interface Cluster { anchor: RingEntry; entries: RingEntry[]; maxDistance: number; }

export default function RingDetection() {
  const [rings, setRings] = useState<RingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [threshold, setThreshold] = useState(8);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function load() { setLoading(true); setError(""); try { setRings(await listRings()); } catch { setError("The fingerprint index could not be loaded."); } finally { setLoading(false); } }
  useEffect(() => { void load(); }, []);

  const clusters = useMemo<Cluster[]>(() => {
    const result: Cluster[] = []; const used = new Set<string>();
    for (const anchor of rings) {
      if (used.has(anchor.id)) continue;
      const entries = [anchor]; let maxDistance = 0; used.add(anchor.id);
      for (const candidate of rings) {
        if (used.has(candidate.id)) continue;
        const distance = hamming(anchor.perceptual_hash, candidate.perceptual_hash);
        if (distance <= threshold) { entries.push(candidate); used.add(candidate.id); maxDistance = Math.max(maxDistance, distance); }
      }
      result.push({ anchor, entries, maxDistance });
    }
    return result.sort((a, b) => b.entries.length - a.entries.length);
  }, [rings, threshold]);
  const linked = clusters.filter((cluster) => cluster.entries.length > 1);
  const selectedCluster = clusters.find((cluster) => cluster.anchor.id === selected) ?? linked[0] ?? clusters[0];

  return (
    <div className="page-stack">
      <section className="page-lead"><div><span className="eyebrow"><span /> Cross-document intelligence</span><h2>Find the pattern behind the paperwork.</h2><p>Perceptual hashes expose shared visual templates even when vendor names, invoice numbers, and amounts are changed.</p></div><div className="lead-stat"><strong>{linked.length}</strong><span>possible rings</span></div></section>
      <div className="summary-strip"><Stat value={rings.length} label="Documents indexed" /><Stat value={linked.reduce((sum, group) => sum + group.entries.length, 0)} label="Linked submissions" tone="critical" /><Stat value={clusters.filter((group) => group.entries.length === 1).length} label="Unique fingerprints" tone="clear" /><Stat value={threshold} label="Hamming threshold" tone="review" /></div>
      <section className="surface ring-controls"><div><span className="section-index">MATCH SENSITIVITY</span><h3>Fingerprint threshold</h3></div><label><span>Strict</span><input type="range" min="2" max="16" step="1" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} /><span>Broad</span><strong>{threshold}</strong></label><button className="button button-outline" onClick={load}>Refresh index</button></section>
      {error && <div className="inline-error">{error}</div>}
      {loading ? <div className="report-skeleton"><div /><div /><span>Comparing document fingerprints…</span></div> : rings.length === 0 ? <section className="empty-report"><h2>The fingerprint index is empty.</h2><p>Run at least two document investigations to compare visual signatures.</p><Link className="button button-primary" to="/app/live">Submit documents</Link></section> : (
        <div className="ring-layout">
          <section className="surface cluster-list"><div className="surface-heading"><div><span className="section-index">CLUSTERS</span><h3>Similarity groups</h3></div><span>{clusters.length} groups</span></div>{clusters.map((cluster, index) => <button key={cluster.anchor.id} className={`cluster-row ${selectedCluster?.anchor.id === cluster.anchor.id ? "active" : ""}`} onClick={() => setSelected(cluster.anchor.id)}><span className="cluster-number">{String(index + 1).padStart(2, "0")}</span><span><strong>{cluster.entries.length > 1 ? "Possible fraud ring" : "Unique document"}</strong><small>{cluster.entries.length} document{cluster.entries.length === 1 ? "" : "s"} · nearest distance {cluster.maxDistance}</small></span><span className={`status-chip ${cluster.entries.length > 1 ? "status-critical" : "status-clear"}`}>{cluster.entries.length > 1 ? "linked" : "unique"}</span></button>)}</section>
          <section className="surface cluster-detail"><div className="surface-heading"><div><span className="section-index">NETWORK VIEW</span><h3>{selectedCluster?.entries.length ?? 0} connected submissions</h3></div><span className="mono">pHash · 64 bit</span></div>{selectedCluster && <><div className="fingerprint-orbit"><div className="orbit-core"><Link to={`/app/verdict?id=${selectedCluster.anchor.id}`}>Open<br />anchor</Link></div>{selectedCluster.entries.map((entry, index) => <div key={entry.id} className={`orbit-node orbit-${Math.min(index, 4)}`} title={entry.vendor ?? entry.filename}><span>{initials(entry.vendor ?? entry.filename)}</span></div>)}</div><div className="fingerprint-list">{selectedCluster.entries.map((entry) => <article key={entry.id}><span className="document-glyph"><FileIcon /></span><div><strong>{entry.vendor ?? "Unknown vendor"}</strong><small>{entry.filename} · {fmtRelative(entry.created_at)}</small><code>{entry.perceptual_hash}</code></div><div><span>distance {hamming(selectedCluster.anchor.perceptual_hash, entry.perceptual_hash)}</span><Link to={`/app/verdict?id=${entry.id}`}>Open report →</Link></div></article>)}</div></>}</section>
        </div>
      )}
    </div>
  );
}
function Stat({ value, label, tone = "" }: { value: number; label: string; tone?: string }) { return <div className={tone}><strong>{value}</strong><span>{label}</span></div>; }
function hamming(a: string, b: string) { try { const bits = (BigInt(`0x${a}`) ^ BigInt(`0x${b}`)).toString(2); return bits.split("1").length - 1; } catch { return 64; } }
function initials(value: string) { return value.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase(); }
function FileIcon(){return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M7 3h7l4 4v14H7z"/><path d="M14 3v5h5"/></svg>}
