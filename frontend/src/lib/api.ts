

const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
    this.name = "ApiError";
  }
}

export interface AuthUser {
  id: string;
  name: string;
  email: string;
  role: string;
}

export interface AgentStatus {
  // free-form string coming from the backend
  [k: string]: string;
}

export interface DocumentRecord {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  perceptual_hash: string;
  image_path: string;
  ela_overlay_path: string | null;
  vendor: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  total_amount: number | null;
  bank_account?: string | null;
  bank_routing?: string | null;
  gstin?: string | null;
  pan?: string | null;
  ifsc_code?: string | null;
  decision: "approved" | "escalated" | "rejected" | "pending";
  risk_score: number | null;
  summary: string | null;
  created_at: string;
  reviewed_at: string | null;
  reviewer_notes: string | null;
}

export interface FullPayload {
  extraction: Extraction | null;
  forensics: Forensics | null;
  policy: Policy | null;
  history: History | null;
  ring: Ring | null;
  verdict: Verdict | null;
  agent_status: Record<string, string>;
  errors: Record<string, string>;
}

export interface Extraction {
  vendor: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  total_amount: number | null;
  currency: string;
  line_items: Array<{ description: string; quantity: number; unit_price: number; amount: number }>;
  raw_text: string;
  ocr_engine: string;
  confidence: number;
  bank_account?: string | null;
  bank_routing?: string | null;
  gstin?: string | null;
  pan?: string | null;
  ifsc_code?: string | null;
}

export interface TamperHotspot {
  box: { x: number; y: number; width: number; height: number };
  intensity: number;
  rank: number;
}

export interface SaliencyResult {
  score: number;
  peak_intensity: number;
  concentration: number;
  hotspots: TamperHotspot[];
  channels: Record<string, number>;
  overlay_path: string | null;
}

export interface Forensics {
  ela_score: number;
  ela_suspicious_ratio: number;
  ela_overlay_path: string | null;
  flagged_region: { x: number; y: number; width: number; height: number } | null;
  metadata_signals: Record<string, unknown>;
  metadata_score: number;
  font_inconsistency_score: number;
  composite_score: number;
  flags: Array<{ code: string; label: string; severity: string; detail: string; score: number }>;
  perceptual_hash: string;
  saliency?: SaliencyResult | null;
  uncertainty?: { passes: number; mean_score: number; stddev: number; confidence: number; epistemic_risk: number } | null;
  patch_localization?: { score: number; overlap_score: number; matched_fields: string[] } | null;
  fusion?: { score: number; contributions: Array<{ name: string; raw: number; weighted: number }> } | null;
}

export interface PolicyCitation {
  clause_id: string;
  clause_title: string;
  snippet: string;
  relevance: number;
  status?: string;
}

export interface Policy {
  score: number;
  compliance_score?: number;
  violated_clauses: PolicyCitation[];
  passed_clauses?: PolicyCitation[];
  rationale: string;
  summary?: string;
}

export interface PriorInvoiceSummary {
  document_id: string;
  filename: string;
  invoice_number: string | null;
  invoice_date: string | null;
  total_amount: number;
  decision: string;
  risk_score: number | null;
  created_at: string;
  bank_account_masked?: string | null;
  ifsc_code?: string | null;
  gstin?: string | null;
}

export interface History {
  score: number;
  vendor_prior_submissions: number;
  vendor_avg_amount: number;
  vendor_max_amount: number;
  vendor_min_amount?: number;
  vendor_total_spend?: number;
  vendor_stddev: number;
  first_seen_date?: string | null;
  last_seen_date?: string | null;
  trust_status?: "verified" | "established" | "new" | "flagged" | string;
  known_bank_accounts?: string[];
  known_ifsc_codes?: string[];
  prior_invoices?: PriorInvoiceSummary[];
  flags: Array<{ code: string; label: string; severity: string; detail: string; score: number }>;
}

export interface Ring {
  score: number;
  matches: Array<{
    matched_document_id: string;
    matched_filename: string;
    hamming_distance: number;
    matched_at: string;
    matched_vendor: string | null;
  }>;
  total_indexed: number;
}

export interface Verdict {
  risk_score: number;
  recommendation: "approved" | "escalated" | "rejected" | "pending";
  summary: string;
  findings: Array<{
    agent: string;
    status: string;
    score: number;
    headline: string;
    detail: string;
  }>;
}

export interface AuditEntry {
  id: number;
  document_id: string;
  filename: string;
  action: "approved" | "escalated" | "rejected";
  actor: string;
  notes: string | null;
  created_at: string;
}

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null) as { detail?: string } | null;
    throw new ApiError(res.status, body?.detail ?? `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const withCredentials: RequestInit = { credentials: "include" };

export async function authMe(): Promise<AuthUser> {
  return jsonOrThrow(await fetch(`${BASE}/api/auth/me`, withCredentials));
}

export async function login(email: string, password: string): Promise<AuthUser> {
  return jsonOrThrow(await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  }));
}

export async function signup(name: string, email: string, password: string): Promise<AuthUser> {
  return jsonOrThrow(await fetch(`${BASE}/api/auth/signup`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, email, password }),
  }));
}

export async function logout(): Promise<void> {
  await jsonOrThrow<void>(await fetch(`${BASE}/api/auth/logout`, {
    method: "POST",
    credentials: "include",
  }));
}

export async function uploadDocument(file: File): Promise<{ document_id: string; status_url: string; stream_url: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/api/documents`, { method: "POST", body: form });
  return jsonOrThrow(res);
}

export async function listDocuments(): Promise<DocumentRecord[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/documents`));
}

export async function getDocument(id: string): Promise<DocumentRecord> {
  return jsonOrThrow(await fetch(`${BASE}/api/documents/${id}`));
}

export async function getPayload(id: string): Promise<{ status: string; payload: FullPayload | null }> {
  const res = await jsonOrThrow<{ status: string; payload: FullPayload | string | null }>(
    await fetch(`${BASE}/api/documents/${id}/payload`),
  );
  if (res && typeof res.payload === "string") {
    try {
      res.payload = JSON.parse(res.payload);
    } catch {
      // noop
    }
  }
  return res as { status: string; payload: FullPayload | null };
}

export async function postDecision(id: string, body: { action: string; actor?: string; notes?: string }): Promise<{ ok: boolean; audit: AuditEntry }> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/documents/${id}/decision`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function listAudit(): Promise<AuditEntry[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/audit`));
}

export async function listRings(): Promise<Array<{ id: string; filename: string; vendor: string | null; created_at: string; perceptual_hash: string }>> {
  return jsonOrThrow(await fetch(`${BASE}/api/rings`));
}

export async function getHealth(): Promise<{
  status: string;
  version: string;
  llm_provider: string;
  llm_model: string | null;
  tesseract: boolean;
  policy_path: string;
}> {
  return jsonOrThrow(await fetch(`${BASE}/api/health`));
}


export function streamUrl(id: string): string {
  return `${BASE}/api/documents/${id}/stream`;
}

export function documentFileUrl(id: string): string {
  return `${BASE}/api/documents/${id}/file`;
}

export function fileUrl(
  path: string | null | undefined,
): string | null {
  if (!path) return null;

  if (
    path.startsWith("http://") ||
    path.startsWith("https://")
  ) {
    return path;
  }

  const clean = path.replace(/^\/+/, "");
  const relativePath = clean.startsWith("data/") ? `/${clean}` : `/data/${clean}`;

  return `${BASE}${relativePath}`;
}