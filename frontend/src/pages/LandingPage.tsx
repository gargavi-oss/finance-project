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
          <a href="#workflow">Workflow</a>
          <a href="#proof">Why Nexora</a>
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
