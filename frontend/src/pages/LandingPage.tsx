import { Link } from "react-router-dom";
import Brand from "../components/Brand";

const AGENTS = [
  ["01", "Extraction", "Turns noisy invoices into structured, reviewable facts."],
  ["02", "Forensics", "Reads compression residue, metadata, and visual inconsistencies."],
  ["03", "Policy", "Grounds every exception in your expense-policy language."],
  ["04", "History", "Compares the claim against the vendor's behavioural baseline."],
  ["05", "Ring detection", "Links visually similar documents across separate submissions."],
  ["06", "Verdict", "Combines the evidence into a defensible risk recommendation."],
];

export default function LandingPage() {
  return (
    <div className="marketing-shell">
      <header className="marketing-nav">
        <Brand />
          <nav aria-label="Primary navigation">
            <a href="#system">The system</a>
            <a href="#why">Why we win</a>
            <a href="#workflow">Workflow</a>
          </nav>
        <div className="marketing-actions">
          <Link className="button button-quiet" to="/login">Sign in</Link>
          <Link className="button button-primary" to="/signup">Create workspace</Link>
        </div>
      </header>

      <main>
        <section className="hero-section">
          <div className="hero-copy reveal-up">
            <div className="eyebrow"><span /> Evidence intelligence for finance teams</div>
            <h1>Know what changed.<br />Prove why it matters.</h1>
            <p>DocForensic AI turns every invoice into an evidence dossier—pixel forensics, policy context, vendor history, and cross-document patterns in one auditable decision trail.</p>
            <div className="hero-actions">
              <Link className="button button-primary button-large" to="/signup">Start investigating</Link>
              <Link className="button button-outline button-large" to="/login">Open control room</Link>
            </div>
            <div className="trust-row">
              <span>Human-in-the-loop by design</span>
              <span>Six specialised agents</span>
              <span>Explainable 0–100 risk score</span>
            </div>
          </div>

          <div className="hero-visual reveal-up delay-1" aria-label="Example evidence pipeline">
            <div className="case-header">
              <div>
                <span className="case-kicker">Case DF-04182</span>
                <strong>Cloud Hosting Co.</strong>
              </div>
              <span className="status-chip status-review">Review required</span>
            </div>
            <div className="score-display">
              <div>
                <span>Composite risk</span>
                <strong>78</strong><small>/100</small>
              </div>
              <svg className="score-ring" viewBox="0 0 120 120">
                <circle cx="60" cy="60" r="49" className="ring-track" />
                <circle cx="60" cy="60" r="49" className="ring-progress" pathLength="100" />
              </svg>
            </div>
            <div className="signal-list">
              <Signal label="Forensics" value="Edited total region" level="high" />
              <Signal label="Policy" value="Approval threshold exceeded" level="medium" />
              <Signal label="History" value="4.2× vendor average" level="medium" />
              <Signal label="Ring detection" value="2 linked documents" level="high" />
            </div>
            <div className="evidence-ticker">
              <span className="live-dot" /> Evidence graph updated 0.8s ago
              <span className="mono">phash 8f0f…745c</span>
            </div>
          </div>
        </section>

        <section className="metrics-strip" id="proof">
          <div><strong>6</strong><span>specialised agents</span></div>
          <div><strong>&lt; 12s</strong><span>typical evidence pass</span></div>
          <div><strong>100%</strong><span>decisions logged</span></div>
          <div><strong>1 view</strong><span>from upload to verdict</span></div>
        </section>

        <section className="agents-section" id="system">
          <div className="section-intro">
            <div className="eyebrow"><span /> One document. Six points of view.</div>
            <h2>A control system built for sceptical reviewers.</h2>
            <p>Each agent owns a narrow question. The verdict arrives only after the evidence has been independently assembled and cross-checked.</p>
          </div>
          <div className="agent-ledger">
            {AGENTS.map(([number, title, copy]) => (
              <article key={number} className="agent-row">
                <span className="agent-number">{number}</span>
                <h3>{title}</h3>
                <p>{copy}</p>
                <span className="agent-arrow" aria-hidden="true">↗</span>
              </article>
            ))}
          </div>
        </section>

        <section className="why-section" id="why">
          <div className="section-intro">
            <div className="eyebrow"><span /> Why DocForensic AI wins</div>
            <h2>Every other approach is blind to the edit.</h2>
            <p>The tools finance teams already use each miss the same thing — the moment a number on the page is changed after the invoice was issued. Here is exactly where they fail, and how we catch it.</p>
          </div>

          <div className="failure-grid">
            <article className="failure-card">
              <div className="failure-head">
                <span className="failure-tool">Traditional OCR &amp; Document AI</span>
                <span className="failure-tag">Semantic Blindness</span>
              </div>
              <div className="failure-row"><b>Core failure</b><span>Reads text but cannot tell a re-typed total from a genuine one. No pixel forensics — the number is taken as ground truth.</span></div>
              <div className="failure-row"><b>The exploit</b><span className="exploit">Edit the figure, re-export the file, and OCR reads the forged number as if it were original.</span></div>
            </article>

            <article className="failure-card">
              <div className="failure-head">
                <span className="failure-tool">Generative AI &amp; Vision LLMs</span>
                <span className="failure-tag">Patch Downsampling</span>
              </div>
              <div className="failure-row"><b>Core failure</b><span>Downsample the image before they "see" it. A spliced total is a tiny patch that falls below their resolution — and they hallucinate plausibility.</span></div>
              <div className="failure-row"><b>The exploit</b><span className="exploit">Subtle edits sit under their visual floor, so the model confidently "confirms" the forged amount.</span></div>
            </article>

            <article className="failure-card">
              <div className="failure-head">
                <span className="failure-tool">ERP &amp; Rule Engines</span>
                <span className="failure-tag">Ring Blindness</span>
              </div>
              <div className="failure-row"><b>Core failure</b><span>Each invoice is assessed in isolation. No cross-document fingerprint, so the same forgery resubmitted under a different vendor name is invisible.</span></div>
              <div className="failure-row"><b>The exploit</b><span className="exploit">Submit one tampered template across many fake vendors — every copy passes on its own.</span></div>
            </article>

            <article className="failure-card">
              <div className="failure-head">
                <span className="failure-tool">Manual Human Auditing</span>
                <span className="failure-tag">Invisibility</span>
              </div>
              <div className="failure-row"><b>Core failure</b><span>No human can see re-compression residue or recall every prior submission. At volume, attention is the bottleneck.</span></div>
              <div className="failure-row"><b>The exploit</b><span className="exploit">Low-value, high-volume claims blend into the queue and slip through unreviewed.</span></div>
            </article>
          </div>

          <div className="why-summary">
            <div className="eyebrow"><span /> The difference</div>
            <h3>DocForensic AI treats the document as evidence, not text.</h3>
            <ul>
              <li><span><b>Pixel forensics</b> — ELA, metadata and saliency catch the re-encode, not just the words.</span></li>
              <li><span><b>Completeness checks</b> — a total that doesn't reconcile with its line items is flagged on its own.</span></li>
              <li><span><b>Ring detection</b> — perceptual-hash fingerprints link the same forgery across vendors.</span></li>
              <li><span><b>Cross-modal checks</b> — printed total vs computed total, surfaced as a contradiction.</span></li>
              <li><span><b>Explainable score</b> — every verdict cites the agents and regions behind it.</span></li>
              <li><span><b>Human in the loop</b> — the agent recommends; your analyst decides and the audit logs it.</span></li>
            </ul>
          </div>
        </section>

        <section className="workflow-section" id="workflow">
          <div className="workflow-copy">
            <div className="eyebrow eyebrow-light"><span /> Investigation workflow</div>
            <h2>From receipt to reasoned decision—without losing the chain of evidence.</h2>
            <p>Upload once, watch every agent work, inspect the complete dossier, and record a human decision that stays traceable.</p>
            <Link className="button button-paper button-large" to="/signup">Build your first case</Link>
          </div>
          <ol className="workflow-steps">
            <li><b>01</b><div><strong>Submit</strong><span>Invoice, receipt, PDF, or image</span></div></li>
            <li><b>02</b><div><strong>Investigate</strong><span>Live evidence pipeline with six agents</span></div></li>
            <li><b>03</b><div><strong>Review</strong><span>Full report, source document, and citations</span></div></li>
            <li><b>04</b><div><strong>Decide</strong><span>Approve, escalate, or reject with notes</span></div></li>
          </ol>
        </section>

        <section className="final-cta">
          <div>
            <span className="case-kicker">Team Nexora · NioHack 2026</span>
            <h2>Fraud leaves a pattern.<br />We make it reviewable.</h2>
          </div>
          <Link className="button button-primary button-large" to="/signup">Enter DocForensic AI</Link>
        </section>
      </main>

      <footer className="marketing-footer">
        <Brand />
        <p>Evidence-first document review for modern finance operations.</p>
        <span>© 2026 Team Nexora</span>
      </footer>
    </div>
  );
}

function Signal({ label, value, level }: { label: string; value: string; level: "high" | "medium" }) {
  return <div className="signal-row"><span className={`signal-bar ${level}`} /><strong>{label}</strong><span>{value}</span></div>;
}
