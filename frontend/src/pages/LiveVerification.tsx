import { useRef, useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { uploadDocument, streamUrl, getPayload, type FullPayload } from "../lib/api";
import { AGENT_DISPLAY, AGENT_ORDER, fmtCurrency, severityClass } from "../lib/format";

interface RunState {
  documentId: string;
  filename: string;
  sizeLabel: string;
  payload: FullPayload | null;
  agentStatus: Record<string, string>;
  log: Array<{ ts: number; agent: string; msg: string }>;
  complete: boolean;
  error?: string;
}

export default function LiveVerification() {
  const [selected, setSelected] = useState<File | null>(null);
  const [run, setRun] = useState<RunState | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const activePollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (activePollRef.current) {
        clearInterval(activePollRef.current);
      }
    };
  }, []);

  function chooseFile(file: File) {
    if (file.size > 25 * 1024 * 1024) {
      setRun({ documentId: "", filename: file.name, sizeLabel: "", payload: null, agentStatus: {}, log: [], complete: false, error: "Files must be smaller than 25 MB." });
      return;
    }
    setSelected(file);
    setRun(null);
  }

  async function loadDemo(name: string) {
    setUploading(true);
    try {
      const response = await fetch(`/data/invoices/${name}`);
      if (!response.ok) throw new Error("Demo document is unavailable.");
      const blob = await response.blob();
      chooseFile(new File([blob], name, { type: "image/png" }));
    } catch (error) {
      setRun({ documentId: "", filename: name, sizeLabel: "", payload: null, agentStatus: {}, log: [], complete: false, error: error instanceof Error ? error.message : "Unable to load the demo." });
    } finally {
      setUploading(false);
    }
  }

  async function startInvestigation() {
    if (!selected || uploading) return;
    setUploading(true);
    if (activePollRef.current) {
      clearInterval(activePollRef.current);
      activePollRef.current = null;
    }
    try {
      const { document_id } = await uploadDocument(selected);
      setRun({
        documentId: document_id,
        filename: selected.name,
        sizeLabel: formatSize(selected.size),
        payload: null,
        agentStatus: Object.fromEntries(AGENT_ORDER.map((agent) => [agent, "queued"])),
        log: [{ ts: Date.now(), agent: "system", msg: `Case ${document_id} accepted` }],
        complete: false,
      });

      let finished = false;
      const cleanup = () => {
        finished = true;
        if (activePollRef.current) {
          clearInterval(activePollRef.current);
          activePollRef.current = null;
        }
      };

      const source = new EventSource(streamUrl(document_id));
      source.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          updateFromEvent(parsed, setRun);
          if (parsed.event === "pipeline_done") {
            cleanup();
            source.close();
          }
        } catch {
          // If SSE event is malformed, polling will still recover state
        }
      };

      source.onerror = () => {
        source.close();
        // Do not fail immediately: polling fallback will retrieve complete dossier from DB
      };

      // Resilient fallback: poll /payload every 1 second until verdict is settled
      activePollRef.current = window.setInterval(async () => {
        if (finished) return;
        try {
          const res = await getPayload(document_id);
          if (res.payload) {
            const p = res.payload;
            const isComplete = !!p.verdict || Object.keys(p.errors ?? {}).length > 0;
            setRun((prev) => {
              if (!prev || prev.complete) return prev;
              return {
                ...prev,
                payload: p,
                agentStatus: { ...prev.agentStatus, ...(p.agent_status ?? {}) },
                complete: isComplete,
              };
            });
            if (isComplete) {
              cleanup();
              source.close();
            }
          }
        } catch {
          // Ignore transient poll fetch errors while server processes
        }
      }, 1000);

    } catch (error) {
      setRun({ documentId: "", filename: selected.name, sizeLabel: formatSize(selected.size), payload: null, agentStatus: {}, log: [], complete: false, error: error instanceof Error ? error.message : "Upload failed." });
    } finally {
      setUploading(false);
    }
  }

  const progress = run ? Math.round((AGENT_ORDER.filter((agent) => !["queued", "running"].includes(run.agentStatus[agent] ?? "queued")).length / AGENT_ORDER.length) * 100) : 0;

  return (
    <div className="page-stack">
      <section className="page-lead">
        <div><span className="eyebrow"><span /> New evidence case</span><h2>Investigate a document in real time.</h2><p>Choose an invoice or receipt. Six specialised agents will assemble the extraction, image evidence, policy context, vendor history, linked fingerprints, and final verdict.</p></div>
        <div className="lead-stat"><strong>{run?.complete ? "Complete" : run ? `${progress}%` : "Ready"}</strong><span>pipeline status</span></div>
      </section>

      <div className="verification-grid">
        <section className="surface upload-surface">
          <div className="surface-heading"><div><span className="section-index">01 / INPUT</span><h3>Source document</h3></div><span className="file-types">PDF · PNG · JPG</span></div>
          <button
            type="button"
            className={`drop-zone ${dragging ? "dragging" : ""}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files[0]; if (file) chooseFile(file); }}
          >
            <span className="drop-icon"><UploadIcon /></span>
            <strong>{selected ? selected.name : "Drop evidence here"}</strong>
            <span>{selected ? `${formatSize(selected.size)} · Ready to investigate` : "or choose a file from your computer"}</span>
            <small>Maximum file size 25 MB</small>
          </button>
          <input ref={inputRef} hidden type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={(event) => { const file = event.target.files?.[0]; if (file) chooseFile(file); }} />

          <div className="demo-row">
            <span>Try a prepared case</span>
            <button disabled={uploading} onClick={() => loadDemo("INV-2026-04182.png")}>Tampered invoice</button>
            <button disabled={uploading} onClick={() => loadDemo("ring_alpha_supplies.png")}>Ring sample</button>
          </div>

          {run?.error && <div className="inline-error" role="alert">{run.error}</div>}
          <button className="button button-primary button-large investigation-button" disabled={!selected || uploading || (!!run && !run.complete && !!run.documentId)} onClick={startInvestigation}>
            {uploading ? "Preparing evidence…" : run && !run.complete ? "Investigation running" : "Start six-agent investigation"}<span>→</span>
          </button>
        </section>

        <section className="surface pipeline-surface">
          <div className="surface-heading"><div><span className="section-index">02 / PIPELINE</span><h3>Agent activity</h3></div><span className={run ? "status-chip status-live" : "status-chip"}><i />{run ? run.complete ? "Complete" : "Live" : "Waiting"}</span></div>
          <div className="pipeline-progress"><span style={{ transform: `scaleX(${progress / 100})` }} /></div>
          <ol className="agent-timeline" aria-live="polite">
            {AGENT_ORDER.map((agent, index) => {
              const status = run?.agentStatus[agent] ?? "queued";
              const info = AGENT_DISPLAY[agent];
              return <li key={agent} className={`agent-step ${status}`}><span className="agent-node">{String(index + 1).padStart(2, "0")}</span><div><strong>{info.label}</strong><small>{latestMessage(run, agent) || info.subtitle}</small></div><span className={severityClass(status)}>{status}</span></li>;
            })}
          </ol>
          {!run && <div className="pipeline-empty">Your agent trace will appear here as soon as a document is submitted.</div>}
        </section>
      </div>

      {run?.payload && <EvidenceSnapshot run={run} />}
    </div>
  );
}

function EvidenceSnapshot({ run }: { run: RunState }) {
  const extraction = run.payload?.extraction;
  const forensics = run.payload?.forensics;
  const verdict = run.payload?.verdict;
  return (
    <section className="surface evidence-snapshot">
      <div className="surface-heading"><div><span className="section-index">03 / EVIDENCE</span><h3>{run.complete ? "Investigation complete" : "Evidence arriving"}</h3></div>{run.complete && <Link className="button button-primary" to={`/app/verdict?id=${run.documentId}`}>Open complete report <span>→</span></Link>}</div>
      <div className="snapshot-grid">
        <div className="snapshot-primary"><span>Vendor</span><strong>{extraction?.vendor ?? "Reading…"}</strong><small>{extraction?.invoice_number ?? run.filename}</small></div>
        <Metric label="Claimed amount" value={fmtCurrency(extraction?.total_amount, extraction?.currency)} />
        <Metric label="OCR confidence" value={extraction ? `${Math.round(extraction.confidence * 100)}%` : "—"} />
        <Metric label="Forensic risk" value={forensics ? `${Math.round(forensics.composite_score * 100)}%` : "—"} />
        <Metric label="Final risk" value={verdict ? `${verdict.risk_score}/100` : "Assembling"} tone={verdict && verdict.risk_score >= 50 ? "danger" : ""} />
      </div>
    </section>
  );
}

function Metric({ label, value, tone = "" }: { label: string; value: string; tone?: string }) { return <div className={`snapshot-metric ${tone}`}><span>{label}</span><strong>{value}</strong></div>; }
function latestMessage(run: RunState | null, agent: string) { return run?.log.filter((entry) => entry.agent === agent).at(-1)?.msg; }
function formatSize(bytes: number) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function updateFromEvent(event: any, setRun: React.Dispatch<React.SetStateAction<RunState | null>>) {
  setRun((previous) => {
    if (!previous) return previous;
    if (event.event === "agent_start") return { ...previous, agentStatus: { ...previous.agentStatus, [event.agent]: "running" }, log: [...previous.log, { ts: Date.now(), agent: event.agent, msg: "Analysing evidence" }] };
    if (event.event === "agent_done") {
      const next = event.result as FullPayload;
      return { ...previous, payload: next, agentStatus: next.agent_status ?? previous.agentStatus, log: [...previous.log, { ts: Date.now(), agent: event.agent, msg: "Evidence recorded" }] };
    }
    if (event.event === "pipeline_done") return { ...previous, payload: event.state as FullPayload, agentStatus: event.state.agent_status ?? previous.agentStatus, complete: true, log: [...previous.log, { ts: Date.now(), agent: "system", msg: "Dossier complete" }] };
    if (event.event === "error") return { ...previous, error: `${event.agent}: ${event.detail}` };
    return previous;
  });
}
function UploadIcon(){return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M12 16V4m0 0L7 9m5-5 5 5"/><path d="M5 15v4h14v-4"/></svg>}
