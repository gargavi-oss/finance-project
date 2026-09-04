/** Shared types & helpers used across pages. */

export type Severity = "passed" | "review" | "high" | "clear" | "running" | "queued" | "error";

export const SEVERITY_LABELS: Record<Severity, string> = {
  passed: "Passed",
  review: "Review",
  high: "High",
  clear: "Clear",
  running: "Running",
  queued: "Queued",
  error: "Error",
};

export function severityClass(s: string | undefined): string {
  switch ((s ?? "").toLowerCase()) {
    case "high":
      return "pill-risk";
    case "review":
      return "pill-warn";
    case "running":
      return "pill-info";
    case "queued":
      return "pill-mute";
    case "error":
      return "pill-risk";
    case "clear":
      return "pill-ok";
    case "passed":
      return "pill-ok";
    default:
      return "pill-mute";
  }
}

export const AGENT_DISPLAY: Record<string, { label: string; subtitle: string }> = {
  extraction: { label: "Extraction", subtitle: "Reading document structure" },
  forensics: { label: "Forensics", subtitle: "Inspecting pixels & metadata" },
  policy: { label: "Policy Compliance", subtitle: "Checking finance policies" },
  history: { label: "Vendor History", subtitle: "Comparing prior submissions" },
  ring: { label: "Ring-Detection", subtitle: "Searching linked fingerprints" },
  verdict: { label: "Verdict", subtitle: "Assembling evidence report" },
};

export const AGENT_ORDER = ["extraction", "forensics", "policy", "history", "ring", "verdict"] as const;

export function fmtCurrency(n: number | null | undefined, currency = "USD"): string {
  if (n === null || n === undefined) return "—";
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 2 }).format(n);
  } catch {
    return `$${n.toFixed(2)}`;
  }
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}