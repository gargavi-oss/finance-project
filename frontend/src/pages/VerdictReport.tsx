import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import {
  fileUrl,
  documentFileUrl,
  getDocument,
  getPayload,
  listDocuments,
  postDecision,
  type DocumentRecord,
  type FullPayload,
  type TamperHotspot,
} from "../lib/api";
import {
  AGENT_DISPLAY,
  fmtCurrency,
  fmtRelative,
  severityClass,
} from "../lib/format";

export default function VerdictReport() {
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("id");

  const { user } = useAuth();

  const [docId, setDocId] = useState<string | null>(requestedId);
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [payload, setPayload] = useState<FullPayload | null>(null);
  const [recent, setRecent] = useState<DocumentRecord[]>([]);

  const [loading, setLoading] = useState(!!requestedId);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState("");
  const [notice, setNotice] = useState("");

  const [previewMode, setPreviewMode] = useState<"source" | "ela">("source");

  // ---------------------------------------------------------------------------
  // Load recent documents
  // ---------------------------------------------------------------------------

  useEffect(() => {
    listDocuments()
      .then((docs) => {
        setRecent(docs);

        if (!requestedId && docs[0]?.risk_score !== null) {
          selectDocument(docs[0].id);
        }
      })
      .catch(() => {
        setError("Unable to load submitted documents.");
      });
  }, []);

  // ---------------------------------------------------------------------------
  // Load selected document + payload
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (!docId) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    let timer: number | undefined;

  const load = async () => {
  try {
    const [doc, result] = await Promise.all([
      getDocument(docId),
      getPayload(docId),
    ]);

    if (cancelled) return;

    setDocument(doc);

    const pipelineSettled =
      !!result.payload &&
      (!!result.payload.verdict ||
        Object.keys(result.payload.errors ?? {}).length > 0);

    // Show whatever partial evidence we have so far, even while still polling.
    if (result.payload) {
      setPayload(result.payload);
    }

    if (pipelineSettled) {
      setLoading(false);
      setError("");
    } else {
      setLoading(true);
      timer = window.setTimeout(() => {
        load();
      }, 1200);
    }
  } catch {
    if (!cancelled) {
      setLoading(false);
      setError(
        "This report could not be loaded. The case may still be processing.",
      );
    }
  }
};

    setLoading(true);
    setPayload(null);
    setDocument(null);
    setNotice("");

    load();

    return () => {
      cancelled = true;

      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [docId]);

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function selectDocument(id: string) {
    setDocId(id);
    setParams({ id });
  }

  const verdict = payload?.verdict;
  const extraction = payload?.extraction;

  const riskTone = verdict
    ? verdict.risk_score >= 75
      ? "critical"
      : verdict.risk_score >= 50
        ? "review"
        : "clear"
    : "pending";

  const completedAgents = useMemo(
    () =>
      Object.values(payload?.agent_status ?? {}).filter(
        (value) => !["queued", "running", "error"].includes(value),
      ).length,
    [payload],
  );

  // ---------------------------------------------------------------------------
  // Human decision
  // ---------------------------------------------------------------------------

  async function decide(
    action: "approved" | "escalated" | "rejected",
  ) {
    if (!docId) return;

    setBusy(true);
    setNotice("");

    try {
      await postDecision(docId, {
        action,
        actor: user?.name ?? "Risk Analyst",
        notes: notes.trim() || undefined,
      });

      setDocument((previous) =>
        previous
          ? {
              ...previous,
              decision: action,
              reviewer_notes: notes.trim() || null,
              reviewed_at: new Date().toISOString(),
            }
          : previous,
      );

      setRecent((items) =>
        items.map((item) =>
          item.id === docId
            ? {
                ...item,
                decision: action,
              }
            : item,
        ),
      );

      setNotice(
        `Decision recorded: ${action}. The audit trail has been updated.`,
      );
    } catch (err) {
      setNotice(
        err instanceof Error
          ? err.message
          : "Decision could not be recorded.",
      );
    } finally {
      setBusy(false);
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="page-stack">
      <section className="report-toolbar">
        <div className="report-selector">
          <label htmlFor="report-case">Selected case</label>

          <select
            id="report-case"
            value={docId ?? ""}
            onChange={(event) => selectDocument(event.target.value)}
          >
            <option value="" disabled>
              Choose a completed document
            </option>

            {recent.map((item) => (
              <option key={item.id} value={item.id}>
                {item.vendor ?? item.filename} ·{" "}
                {item.risk_score ?? "processing"}
              </option>
            ))}
          </select>
        </div>

        {document && (
          <div className="case-meta">
            <span>Case {document.id}</span>
            <span>{fmtRelative(document.created_at)}</span>

            <span
              className={`status-chip status-${document.decision}`}
            >
              {document.decision}
            </span>
          </div>
        )}
      </section>

      {!docId && <EmptyReport onStart="/app/live" />}

      {docId && loading && <ReportSkeleton />}

      {error && (
        <div className="inline-error">
          {error}

          <button
            onClick={() => {
              if (docId) {
                selectDocument(docId);
              }
            }}
          >
            Retry
          </button>
        </div>
      )}

      {docId &&
        payload &&
        verdict &&
        extraction &&
        document && (
          <>
            {/* ---------------------------------------------------------------- */}
            {/* Verdict Hero                                                     */}
            {/* ---------------------------------------------------------------- */}

            <section
              className={`verdict-hero verdict-${riskTone}`}
            >
              <div className="verdict-score-block">
                <span className="section-index">
                  COMPOSITE RISK
                </span>

                <div className="risk-number">
                  <strong>{verdict.risk_score}</strong>
                  <span>/100</span>
                </div>

                <div className="risk-track">
                  <i
                    style={{
                      transform: `scaleX(${verdict.risk_score / 100})`,
                    }}
                  />
                </div>

                <span
                  className={`status-chip status-${riskTone}`}
                >
                  {verdict.recommendation}
                </span>
              </div>

              <div className="verdict-copy">
                <span className="eyebrow eyebrow-light">
                  <span />
                  Evidence verdict
                </span>

                <h2>
                  {headlineFor(verdict.risk_score)}
                </h2>

                <p>
                  {cleanSummary(verdict.summary)}
                </p>

                <div className="verdict-facts">
                  <span>
                    <strong>{completedAgents}</strong>{" "}
                    agents complete
                  </span>

                  <span>
                    <strong>
                      {verdict.findings.length}
                    </strong>{" "}
                    findings synthesised
                  </span>

                  <span>
                    <strong>
                      {payload.ring?.matches.length ?? 0}
                    </strong>{" "}
                    linked documents
                  </span>
                </div>
              </div>
            </section>

            {/* ---------------------------------------------------------------- */}
            {/* Report Grid                                                       */}
            {/* ---------------------------------------------------------------- */}

            <div className="report-grid">
              {/* SOURCE EVIDENCE */}

              <section className="surface document-viewer">
                <div className="surface-heading">
                  <div>
                    <span className="section-index">
                      SOURCE EVIDENCE
                    </span>

                    <h3>
                      {extraction.vendor ??
                        document.filename}
                    </h3>
                  </div>

                  <div className="viewer-controls">
                    <button
                      className={
                        previewMode === "source"
                          ? "active"
                          : ""
                      }
                      onClick={() =>
                        setPreviewMode("source")
                      }
                    >
                      Original
                    </button>

                    <button
                      className={
                        previewMode === "ela"
                          ? "active"
                          : ""
                      }
                      disabled={
                        !payload.forensics?.ela_overlay_path &&
                        !document.ela_overlay_path
                      }
                      onClick={() =>
                        setPreviewMode("ela")
                      }
                    >
                      ELA map
                    </button>
                    <a
                      href={`${documentFileUrl(document.id)}?download=true`}
                      download={document.filename}
                      target="_blank"
                      rel="noreferrer"
                      aria-label="Open source document"
                    >
                      <DownloadIcon />
                    </a>
                  </div>
                </div>

                <DocumentViewer
                  document={document}
                  payload={payload}
                  mode={previewMode}
                />
              </section>

              {/* DOCUMENT FACTS */}

              <section className="surface report-details">
                <div className="surface-heading">
                  <div>
                    <span className="section-index">
                      DOCUMENT FACTS
                    </span>

                    <h3>Extracted record</h3>
                  </div>

                  <span className="confidence">
                    {Math.round(
                      extraction.confidence * 100,
                    )}
                    % OCR confidence
                  </span>
                </div>

                <dl className="fact-grid">
                  <Fact
                    label="Vendor"
                    value={
                      extraction.vendor ??
                      "Not identified"
                    }
                  />

                  <Fact
                    label="Invoice number"
                    value={
                      extraction.invoice_number ??
                      "Not identified"
                    }
                    mono
                  />

                  <Fact
                    label="Invoice date"
                    value={
                      extraction.invoice_date ??
                      "Not identified"
                    }
                  />

                  <Fact
                    label="Claimed total"
                    value={fmtCurrency(
                      extraction.total_amount,
                      extraction.currency,
                    )}
                    strong
                  />

                  <Fact
                    label="OCR engine"
                    value={extraction.ocr_engine}
                  />

                  <Fact
                    label="Document hash"
                    value={
                      document.sha256.slice(0, 18) +
                      "…"
                    }
                    mono
                  />
                </dl>

                <div className="line-items">
                  <h4>Line items</h4>

                  {extraction.line_items.length ? (
                    extraction.line_items.map(
                      (item, index) => (
                        <div
                          className="line-item"
                          key={`${item.description}-${index}`}
                        >
                          <span>
                            <strong>
                              {item.description}
                            </strong>

                            <small>
                              {item.quantity} ×{" "}
                              {fmtCurrency(
                                item.unit_price,
                                extraction.currency,
                              )}
                            </small>
                          </span>

                          <b>
                            {fmtCurrency(
                              item.amount,
                              extraction.currency,
                            )}
                          </b>
                        </div>
                      ),
                    )
                  ) : (
                    <p className="muted-copy">
                      No line items were extracted.
                    </p>
                  )}
                </div>
              </section>
            </div>

            {/* ---------------------------------------------------------------- */}
            {/* Agent Findings                                                    */}
            {/* ---------------------------------------------------------------- */}

            <section className="surface findings-section">
              <div className="surface-heading">
                <div>
                  <span className="section-index">
                    AGENT FINDINGS
                  </span>

                  <h3>
                    The complete evidence stack
                  </h3>
                </div>

                <span>
                  {verdict.findings.length} evidence
                  conclusions
                </span>
              </div>

              <div className="findings-ledger">
                {verdict.findings.map(
                  (finding, index) => (
                    <article
                      key={finding.agent}
                      className="finding-entry"
                    >
                      <span className="finding-index">
                        {String(index + 1).padStart(
                          2,
                          "0",
                        )}
                      </span>

                      <div className="finding-copy">
                        <div>
                          <h4>
                            {AGENT_DISPLAY[
                              finding.agent
                            ]?.label ??
                              finding.agent}
                          </h4>

                          <span
                            className={severityClass(
                              finding.status,
                            )}
                          >
                            {finding.status}
                          </span>
                        </div>

                        <strong>
                          {finding.headline}
                        </strong>

                        <p>
                          {finding.detail ||
                            "No additional detail reported."}
                        </p>
                      </div>

                      <div className="finding-score">
                        <strong>
                          {Math.round(
                            finding.score * 100,
                          )}
                        </strong>

                        <span>signal</span>
                      </div>
                    </article>
                  ),
                )}
              </div>
            </section>

            {/* ---------------------------------------------------------------- */}
            {/* Evidence Columns                                                  */}
            {/* ---------------------------------------------------------------- */}

            <div className="evidence-columns">
              <TechnicalEvidence payload={payload} />
              <PolicyEvidence payload={payload} />
              <HistoryEvidence payload={payload} />
              <RingEvidence payload={payload} />
            </div>

            {/* ---------------------------------------------------------------- */}
            {/* Decision Panel                                                    */}
            {/* ---------------------------------------------------------------- */}

            <section className="decision-panel">
              <div>
                <span className="eyebrow eyebrow-light">
                  <span />
                  Human decision
                </span>

                <h3>
                  Record the final disposition.
                </h3>

                <p>
                  The model recommends{" "}
                  <strong>
                    {verdict.recommendation}
                  </strong>
                  . Add context before you approve,
                  escalate, or reject.
                </p>
              </div>

              <div className="decision-form">
                <label>
                  Reviewer note

                  <textarea
                    value={notes}
                    onChange={(event) =>
                      setNotes(event.target.value)
                    }
                    placeholder="Optional context for the audit trail…"
                  />
                </label>

                <div className="decision-actions">
                  <button
                    disabled={busy}
                    className="button button-outline-light"
                    onClick={() =>
                      decide("escalated")
                    }
                  >
                    Escalate
                  </button>

                  <button
                    disabled={busy}
                    className="button button-danger"
                    onClick={() =>
                      decide("rejected")
                    }
                  >
                    Reject
                  </button>

                  <button
                    disabled={busy}
                    className="button button-paper"
                    onClick={() =>
                      decide("approved")
                    }
                  >
                    Approve
                  </button>
                </div>

                {notice && (
                  <div
                    className="decision-notice"
                    aria-live="polite"
                  >
                    {notice}
                  </div>
                )}
              </div>
            </section>
          </>
        )}
    </div>
  );
}

// ============================================================================
// Document Viewer
// ============================================================================

function DocumentViewer({
  document,
  payload,
  mode,
}: {
  document: DocumentRecord;
  payload: FullPayload;
  mode: "source" | "ela";
}) {
  // IMPORTANT:
  // /stream is an SSE endpoint and must NOT be used as an <img> source.
  // /file returns the actual uploaded document.
  const source = documentFileUrl(document.id);

  const overlayPath =
    payload.forensics?.ela_overlay_path || document.ela_overlay_path;
  const overlay = overlayPath ? fileUrl(overlayPath) : null;

  const shown =
    mode === "ela" && overlay
      ? overlay
      : source;

  const region = payload.forensics?.flagged_region;
  const saliencyHotspots: TamperHotspot[] = payload.forensics?.saliency?.hotspots ?? [];
  const compositeScore = payload.forensics?.composite_score ?? 0;

  // --- Image loading / error state ---
  const [imgState, setImgState] = useState<"loading" | "loaded" | "error">("loading");

  // --- Track rendered image rect for overlay positioning ---
  const canvasRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [naturalW, setNaturalW] = useState(0);
  const [naturalH, setNaturalH] = useState(0);
  const [imgRect, setImgRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

  // Reset loading state when the image URL changes.
  useEffect(() => {
    setImgState("loading");
  }, [shown]);

  // Measure the rendered image rect within the canvas (object-fit: contain
  // centers the image, so its rendered area != the container).
  const measure = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !img.naturalWidth) return;

    const cRect = canvas.getBoundingClientRect();
    const nw = img.naturalWidth;
    const nh = img.naturalHeight;
    setNaturalW(nw);
    setNaturalH(nh);

    // Compute the rendered image area when object-fit: contain is used.
    const scale = Math.min(cRect.width / nw, cRect.height / nh);
    const rw = nw * scale;
    const rh = nh * scale;
    const rl = (cRect.width - rw) / 2;
    const rt = (cRect.height - rh) / 2;
    setImgRect({ left: rl, top: rt, width: rw, height: rh });
  }, []);

  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const handleLoad = useCallback(() => {
    setImgState("loaded");
    measure();
  }, [measure]);

  const handleError = useCallback(() => {
    console.error("Document image failed to load:", shown);
    setImgState("error");
  }, [shown]);

  // --- Helper: convert pixel coords to overlay percentages ---
  function pxToOverlay(px: number, axis: "x" | "y"): number {
    if (!naturalW || !naturalH) return 0;
    return axis === "x"
      ? (px / naturalW) * 100
      : (px / naturalH) * 100;
  }

  const isClean = !region && compositeScore < 0.4;
  const isTampered = !!region || compositeScore >= 0.4;

  return (
    <div className="document-canvas" ref={canvasRef}>
      {/* Loading state */}
      {imgState === "loading" && shown && (
        <div className="canvas-loading">
          <div className="canvas-spinner" />
          <span>Loading document…</span>
        </div>
      )}

      {/* Error state */}
      {imgState === "error" && (
        <div className="canvas-error">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="10" />
            <path d="M15 9l-6 6M9 9l6 6" />
          </svg>
          <span>Document preview could not be loaded.</span>
          <span style={{ fontSize: 10, opacity: 0.7 }}>The file may still be processing.</span>
        </div>
      )}

      {/* The actual image */}
      {shown ? (
        <img
          ref={imgRef}
          src={shown}
          className={imgState === "loading" ? "loading-hidden" : ""}
          alt={
            mode === "ela"
              ? "Error level analysis map"
              : `Original ${document.filename}`
          }
          onLoad={handleLoad}
          onError={handleError}
        />
      ) : (
        <div className="viewer-empty">
          Preview unavailable
        </div>
      )}

      {/* --- Overlays (only when image is loaded and measured) --- */}
      {imgState === "loaded" && imgRect && (
        <div
          className="canvas-overlay-wrapper"
          style={{
            left: imgRect.left,
            top: imgRect.top,
            width: imgRect.width,
            height: imgRect.height,
          }}
        >
          {/* Flagged region with cross-hair lines */}
          {(mode === "source" || mode === "ela") && region && naturalW > 0 && naturalH > 0 && (
            <>
              {/* Cross-hair horizontal lines (top and bottom of region) */}
              <div
                className="flag-crosshair flag-crosshair-h"
                style={{ top: `${pxToOverlay(region.y, "y")}%` }}
              />
              <div
                className="flag-crosshair flag-crosshair-h"
                style={{ top: `${pxToOverlay(region.y + region.height, "y")}%` }}
              />

              {/* Cross-hair vertical lines (left and right of region) */}
              <div
                className="flag-crosshair flag-crosshair-v"
                style={{ left: `${pxToOverlay(region.x, "x")}%` }}
              />
              <div
                className="flag-crosshair flag-crosshair-v"
                style={{ left: `${pxToOverlay(region.x + region.width, "x")}%` }}
              />

              {/* The flag box itself */}
              <div
                className="flag-box"
                style={{
                  left: `${pxToOverlay(region.x, "x")}%`,
                  top: `${pxToOverlay(region.y, "y")}%`,
                  width: `${pxToOverlay(region.width, "x")}%`,
                  height: `${pxToOverlay(region.height, "y")}%`,
                }}
              >
                <span>Flagged region</span>
              </div>

              {/* Annotation label connected by a line */}
              <div
                className="flag-annotation"
                style={{
                  left: `${pxToOverlay(region.x + region.width, "x")}%`,
                  top: `${pxToOverlay(region.y + region.height / 2, "y")}%`,
                }}
              >
                <div className="flag-connector" />
                <div className="flag-label">
                  Tampering detected
                  <small>
                    {region.width}×{region.height}px · ELA hotspot
                  </small>
                </div>
              </div>
            </>
          )}

          {/* Saliency hotspot overlays (amber boxes) */}
          {(mode === "source" || mode === "ela") && saliencyHotspots.length > 0 && naturalW > 0 && naturalH > 0 &&
            saliencyHotspots.slice(0, 5).map((hotspot, idx) => (
              <div
                key={`hotspot-${idx}`}
                className="hotspot-box"
                style={{
                  left: `${pxToOverlay(hotspot.box.x, "x")}%`,
                  top: `${pxToOverlay(hotspot.box.y, "y")}%`,
                  width: `${pxToOverlay(hotspot.box.width, "x")}%`,
                  height: `${pxToOverlay(hotspot.box.height, "y")}%`,
                  opacity: 0.5 + hotspot.intensity * 0.5,
                }}
              >
                <span>#{hotspot.rank} · {Math.round(hotspot.intensity * 100)}%</span>
              </div>
            ))
          }
        </div>
      )}

      {/* Canvas badge: clean or tampered */}
      {imgState === "loaded" && mode === "source" && isClean && (
        <div className="canvas-badge canvas-badge-clean">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M5 13l4 4L19 7" />
          </svg>
          No tampering detected
        </div>
      )}

      {imgState === "loaded" && mode === "source" && isTampered && region && (
        <div className="canvas-badge canvas-badge-tampered">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 9v4M12 17h.01M12 2L2 22h20L12 2z" />
          </svg>
          Tampering flagged · Score {Math.round(compositeScore * 100)}%
        </div>
      )}

      <div className="viewer-caption">
        <span>
          {mode === "ela"
            ? "Error-level analysis"
            : "Original evidence"}
        </span>

        <code>
          {document.perceptual_hash}
        </code>
      </div>
    </div>
  );
}

// ============================================================================
// Fact
// ============================================================================

function Fact({
  label,
  value,
  mono,
  strong,
}: {
  label: string;
  value: string;
  mono?: boolean;
  strong?: boolean;
}) {
  return (
    <div>
      <dt>{label}</dt>

      <dd
        className={`${mono ? "mono" : ""} ${
          strong ? "strong" : ""
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

// ============================================================================
// Technical Evidence
// ============================================================================

function TechnicalEvidence({
  payload,
}: {
  payload: FullPayload;
}) {
  const f = payload.forensics;

  return (
    <EvidenceCard
      index="A"
      title="Image forensics"
      status={payload.agent_status.forensics}
    >
      {f ? (
        <>
          <EvidenceMeter
            label="ELA anomaly"
            value={f.ela_score}
          />

          <EvidenceMeter
            label="Metadata risk"
            value={f.metadata_score}
          />

          <EvidenceMeter
            label="Font variance"
            value={f.font_inconsistency_score}
          />

          <ul>
            {f.flags.length ? (
              f.flags.map((flag) => (
                <li key={flag.code}>
                  <strong>{flag.label}</strong>
                  <span>{flag.detail}</span>
                </li>
              ))
            ) : (
              <li>
                <strong>
                  No material image anomalies
                </strong>

                <span>
                  The file remains within configured
                  forensic thresholds.
                </span>
              </li>
            )}
          </ul>
        </>
      ) : (
        <p className="muted-copy">
          No forensic result available.
        </p>
      )}
    </EvidenceCard>
  );
}

// ============================================================================
// Policy Evidence
// ============================================================================

function PolicyEvidence({
  payload,
}: {
  payload: FullPayload;
}) {
  const p = payload.policy;

  return (
    <EvidenceCard
      index="B"
      title="Policy review"
      status={payload.agent_status.policy}
    >
      {p ? (
        <>
          <EvidenceMeter
            label="Violation score"
            value={p.score}
          />

          <p className="evidence-rationale">
            {p.rationale ||
              "No policy rationale returned."}
          </p>

          <ul>
            {p.violated_clauses.length ? (
              p.violated_clauses.map((clause) => (
                <li key={clause.clause_id}>
                  <strong>
                    {clause.clause_id} ·{" "}
                    {clause.clause_title}
                  </strong>

                  <span>
                    {clause.snippet}
                  </span>
                </li>
              ))
            ) : (
              <li>
                <strong>
                  Within policy
                </strong>

                <span>
                  No violated clauses were retrieved.
                </span>
              </li>
            )}
          </ul>
        </>
      ) : (
        <p className="muted-copy">
          No policy result available.
        </p>
      )}
    </EvidenceCard>
  );
}

// ============================================================================
// History Evidence
// ============================================================================

function HistoryEvidence({
  payload,
}: {
  payload: FullPayload;
}) {
  const h = payload.history;

  return (
    <EvidenceCard
      index="C"
      title="Vendor history"
      status={payload.agent_status.history}
    >
      {h ? (
        <>
          <div className="mini-stats">
            <div>
              <strong>
                {h.vendor_prior_submissions}
              </strong>

              <span>prior submissions</span>
            </div>

            <div>
              <strong>
                {fmtCurrency(
                  h.vendor_avg_amount,
                )}
              </strong>

              <span>average amount</span>
            </div>

            <div>
              <strong>
                {fmtCurrency(
                  h.vendor_max_amount,
                )}
              </strong>

              <span>previous maximum</span>
            </div>
          </div>

          <ul>
            {h.flags.length ? (
              h.flags.map((flag) => (
                <li key={flag.code}>
                  <strong>{flag.label}</strong>
                  <span>{flag.detail}</span>
                </li>
              ))
            ) : (
              <li>
                <strong>
                  History consistent
                </strong>

                <span>
                  No statistical anomaly was found
                  for this vendor.
                </span>
              </li>
            )}
          </ul>
        </>
      ) : (
        <p className="muted-copy">
          No history result available.
        </p>
      )}
    </EvidenceCard>
  );
}

// ============================================================================
// Ring Evidence
// ============================================================================

function RingEvidence({
  payload,
}: {
  payload: FullPayload;
}) {
  const r = payload.ring;

  return (
    <EvidenceCard
      index="D"
      title="Cross-document links"
      status={payload.agent_status.ring}
    >
      {r ? (
        <>
          <div className="mini-stats">
            <div>
              <strong>
                {r.matches.length}
              </strong>

              <span>fingerprint matches</span>
            </div>

            <div>
              <strong>
                {r.total_indexed}
              </strong>

              <span>documents searched</span>
            </div>
          </div>

          <ul>
            {r.matches.length ? (
              r.matches.map((match) => (
                <li
                  key={
                    match.matched_document_id
                  }
                >
                  <strong>
                    {match.matched_vendor ??
                      match.matched_filename}
                  </strong>

                  <span>
                    Hamming distance{" "}
                    {match.hamming_distance} ·{" "}
                    {fmtRelative(
                      match.matched_at,
                    )}
                  </span>
                </li>
              ))
            ) : (
              <li>
                <strong>
                  No linked documents
                </strong>

                <span>
                  This fingerprint is unique within
                  the current index.
                </span>
              </li>
            )}
          </ul>
        </>
      ) : (
        <p className="muted-copy">
          No ring result available.
        </p>
      )}
    </EvidenceCard>
  );
}

// ============================================================================
// Evidence Card
// ============================================================================

function EvidenceCard({
  index,
  title,
  status,
  children,
}: {
  index: string;
  title: string;
  status?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="surface evidence-card">
      <div className="evidence-card-head">
        <span>{index}</span>

        <h3>{title}</h3>

        <span className={severityClass(status)}>
          {status ?? "unavailable"}
        </span>
      </div>

      {children}
    </section>
  );
}

// ============================================================================
// Evidence Meter
// ============================================================================

function EvidenceMeter({
  label,
  value,
}: {
  label: string;
  value: number;
}) {
  return (
    <div className="evidence-meter">
      <div>
        <span>{label}</span>

        <strong>
          {Math.round(value * 100)}%
        </strong>
      </div>

      <i>
        <b
          style={{
            transform: `scaleX(${Math.min(
              1,
              Math.max(0, value),
            )})`,
          }}
        />
      </i>
    </div>
  );
}

// ============================================================================
// Empty Report
// ============================================================================

function EmptyReport({
  onStart,
}: {
  onStart: string;
}) {
  return (
    <section className="empty-report">
      <span className="empty-mark">
        <ReportIcon />
      </span>

      <h2>
        No completed dossier selected.
      </h2>

      <p>
        Start an investigation or choose a
        processed document from the case selector.
      </p>

      <Link
        className="button button-primary"
        to={onStart}
      >
        Start an investigation
      </Link>
    </section>
  );
}

// ============================================================================
// Loading Skeleton
// ============================================================================

function ReportSkeleton() {
  return (
    <div
      className="report-skeleton"
      aria-label="Loading report"
    >
      <div />
      <div />
      <div />

      <span>
        Assembling the evidence dossier…
      </span>
    </div>
  );
}

// ============================================================================
// Helpers
// ============================================================================

function headlineFor(score: number) {
  return score >= 75
    ? "Material fraud indicators require intervention."
    : score >= 50
      ? "The document needs a focused human review."
      : "Evidence is consistent with a low-risk submission.";
}

function cleanSummary(summary: string) {
  if (!summary) {
    return "The evidence agents completed their review and produced the findings below.";
  }

  let cleaned = summary
    // Strip [demo LLM] prefix.
    .replace(/^\[demo LLM\]\s*/i, "")
    // Strip System / User prompt scaffold leakage if present
    .replace(/^(?:System|User|Prompt):[\s\S]*?(?=(?:The analysis|Key signals|Forensic flags|All evidence|In summary|$))/im, "")
    .replace(/Document:.*?Agent findings:[\s\S]*?(?=(?:Write a|The analysis|Key signals|Forensic flags|In summary|$))/im, "")
    .replace(/Write a \d+-\d+ sentence[\s\S]*$/im, "")
    .replace(/\s{2,}/g, " ")
    .trim();

  return (
    cleaned ||
    "The evidence agents completed their review and produced the findings below."
  );
}

// ============================================================================
// Icons
// ============================================================================

function DownloadIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <path d="M12 4v11m0 0-4-4m4 4 4-4" />
      <path d="M5 19h14" />
    </svg>
  );
}

function ReportIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
    >
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v5h5M10 13h5M10 17h5" />
    </svg>
  );
}