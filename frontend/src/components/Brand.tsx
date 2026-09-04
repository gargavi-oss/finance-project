import { Link } from "react-router-dom";

export default function Brand({ inverted = false, to = "/" }: { inverted?: boolean; to?: string }) {
  return (
    <Link to={to} className={`brand-lockup ${inverted ? "brand-lockup-inverted" : ""}`} aria-label="DocForensic AI home">
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" fill="none">
          <path d="M16 3 27 7v8.2C27 22 22.2 26.5 16 29 9.8 26.5 5 22 5 15.2V7l11-4Z" stroke="currentColor" strokeWidth="2" />
          <path d="M10 16h4l2-6 2.4 11 1.8-5H23" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span>
        <strong>DocForensic</strong>
        <small>AI evidence intelligence</small>
      </span>
    </Link>
  );
}
