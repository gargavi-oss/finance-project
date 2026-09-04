"""Pydantic schemas — the public contract for the API.

These types are used both for FastAPI request/response models and for
internal data passing between agents (the LangGraph state).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class AgentName(str, Enum):
    EXTRACTION = "extraction"
    FORENSICS = "forensics"
    POLICY = "policy"
    HISTORY = "history"
    RING = "ring"
    VERDICT = "verdict"


class AgentStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    HIGH = "high"
    REVIEW = "review"
    CLEAR = "clear"
    ERROR = "error"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DocumentDecision(str, Enum):
    APPROVED = "approved"
    ESCALATED = "escalated"
    REJECTED = "rejected"
    PENDING = "pending"


# --------------------------------------------------------------------------- #
# Sub-results
# --------------------------------------------------------------------------- #


class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


class ExtractedLineItem(BaseModel):
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    amount: float = 0.0


class ExtractionResult(BaseModel):
    vendor: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    total_amount: Optional[float] = None
    currency: str = "INR"
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    raw_text: str = ""
    ocr_engine: str = "tesseract"
    confidence: float = 0.0
    bank_account: Optional[str] = None
    bank_routing: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    ifsc_code: Optional[str] = None
    flagged_regions: list[BoundingBox] = Field(default_factory=list)
    # Schema / business-rule validation (AWS IDP "blueprint" analogue).
    completeness: Optional["CompletenessResult"] = None


class ForensicsFlag(BaseModel):
    code: str
    label: str
    severity: Severity
    detail: str
    score: float = Field(ge=0.0, le=1.0)


# --------------------------------------------------------------------------- #
# Multi-signal tamper model
#
# Ported from the reference architecture in
#   github.com/Aathi-27/multimodal-document-tampering-detection
# which fuses six complementary signals (ELA, Grad-CAM saliency, MC-Dropout
# uncertainty, OCR semantic conflict, OCR confidence, spatial IoU overlap)
# into one weighted risk score. We keep the same six-signal contract but
# replace the TensorFlow/EfficientNetB7 classifier with deterministic,
# dependency-free computer-vision equivalents so the model runs on CPU.
# --------------------------------------------------------------------------- #


class TamperHotspot(BaseModel):
    """A localised region of the page that drives the tamper signal."""

    box: BoundingBox
    intensity: float = Field(ge=0.0, le=1.0)
    rank: int = 1


class SaliencyResult(BaseModel):
    """Spatial saliency — the localisation half of the tamper model.

    Analogous to ``grad_cam.py``: answers *where* on the page the model is
    looking, and how strongly that region drives the tampering decision.
    """

    score: float = Field(ge=0.0, le=1.0)
    peak_intensity: float = Field(ge=0.0, le=1.0)
    concentration: float = Field(ge=0.0, le=1.0)
    hotspots: list[TamperHotspot] = Field(default_factory=list)
    # Per-channel mean intensity, useful for explaining *why* a page is hot.
    channels: dict[str, float] = Field(default_factory=dict)
    overlay_path: Optional[str] = None


class UncertaintyResult(BaseModel):
    """Predictive uncertainty — analogous to ``mc_dropout.py``.

    MC Dropout keeps dropout active and runs N stochastic forward passes,
    using the variance across passes to estimate epistemic uncertainty. We
    apply the same idea at the input/encoder level: N stochastic re-encodes
    of the page, measuring how stable the tamper score is.
    """

    passes: int = 0
    mean_score: float = Field(ge=0.0, le=1.0)
    stddev: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    epistemic_risk: float = Field(ge=0.0, le=1.0)
    per_pass: list[float] = Field(default_factory=list)


class PatchLocalizationResult(BaseModel):
    """Spatial agreement between visual anomaly and semantic fields.

    Analogous to ``patch_localization.py``: an IoU-style overlap between
    anomaly-dense patches and the patches that actually matter (the total,
    the date, the invoice number). Anomaly in a margin is noise; anomaly on
    the total is fraud.
    """

    grid_rows: int = 16
    grid_cols: int = 16
    density_score: float = Field(0.0, ge=0.0, le=1.0)
    overlap_score: float = Field(0.0, ge=0.0, le=1.0)
    score: float = Field(0.0, ge=0.0, le=1.0)
    hotspot_patch_ratio: float = Field(0.0, ge=0.0, le=1.0)
    anomalous_patches: int = 0
    field_patches: int = 0
    matched_fields: list[str] = Field(default_factory=list)


class SignalContribution(BaseModel):
    name: str
    label: str
    value: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)
    contribution: float = Field(ge=0.0, le=1.0)


class SignalFusion(BaseModel):
    """Weighted fusion of the six tamper signals — analogous to ``fusion.py``."""

    score: float = Field(ge=0.0, le=1.0)
    band: str = "low"  # low | medium | high
    contributions: list[SignalContribution] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class CompletenessIssue(BaseModel):
    code: str
    label: str
    severity: Severity
    detail: str
    score: float = Field(ge=0.0, le=1.0)


class CompletenessResult(BaseModel):
    """Schema + business-rule validation of the extracted record.

    Analogous to the "automated completeness and validity checks" performed by
    the Bedrock Data Automation blueprints in the AWS IDP fraud-detection
    guidance: verify the document matches the expected invoice schema and that
    its own numbers are internally consistent.
    """

    required_total: int = 0
    required_present: int = 0
    completeness: float = Field(ge=0.0, le=1.0)
    issues: list[CompletenessIssue] = Field(default_factory=list)
    semantic_conflict_score: float = Field(ge=0.0, le=1.0)
    extraction_confidence_score: float = Field(ge=0.0, le=1.0)
    sharpness: float = Field(ge=0.0, le=1.0)
    tax_reconciled: bool = False
    tax_note: Optional[str] = None


class ForensicsResult(BaseModel):
    ela_score: float = Field(ge=0.0, le=1.0)
    ela_suspicious_ratio: float = Field(ge=0.0, le=1.0)
    ela_overlay_path: Optional[str] = None
    flagged_region: Optional[BoundingBox] = None
    metadata_signals: dict[str, Any] = Field(default_factory=dict)
    metadata_score: float = Field(ge=0.0, le=1.0)
    font_inconsistency_score: float = Field(ge=0.0, le=1.0)
    composite_score: float = Field(ge=0.0, le=1.0)
    flags: list[ForensicsFlag] = Field(default_factory=list)
    perceptual_hash: str

    # --- multi-signal tamper model -----------------------------------------
    saliency: Optional[SaliencyResult] = None
    uncertainty: Optional[UncertaintyResult] = None
    patch_localization: Optional[PatchLocalizationResult] = None
    fusion: Optional[SignalFusion] = None


class PolicyCitation(BaseModel):
    clause_id: str
    clause_title: str
    snippet: str
    relevance: float = Field(ge=0.0, le=1.0)
    status: str = "violated"  # "violated" or "passed"


class PolicyResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)  # higher = more policy-violating risk
    compliance_score: float = Field(default=1.0, ge=0.0, le=1.0)  # 1.0 = 100% compliant
    violated_clauses: list[PolicyCitation] = Field(default_factory=list)
    passed_clauses: list[PolicyCitation] = Field(default_factory=list)
    rationale: str = ""
    summary: str = ""


class HistoryFlag(BaseModel):
    code: str
    label: str
    severity: Severity
    detail: str
    score: float = Field(ge=0.0, le=1.0)


class PriorInvoiceSummary(BaseModel):
    document_id: str
    filename: str
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    total_amount: float = 0.0
    decision: str = "pending"
    risk_score: Optional[int] = None
    created_at: str = ""
    bank_account_masked: Optional[str] = None
    ifsc_code: Optional[str] = None
    gstin: Optional[str] = None


class HistoryResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    vendor_prior_submissions: int = 0
    vendor_avg_amount: float = 0.0
    vendor_max_amount: float = 0.0
    vendor_min_amount: float = 0.0
    vendor_total_spend: float = 0.0
    vendor_stddev: float = 0.0
    first_seen_date: Optional[str] = None
    last_seen_date: Optional[str] = None
    trust_status: str = "new"  # verified, established, new, flagged
    known_bank_accounts: list[str] = Field(default_factory=list)
    known_ifsc_codes: list[str] = Field(default_factory=list)
    prior_invoices: list[PriorInvoiceSummary] = Field(default_factory=list)
    flags: list[HistoryFlag] = Field(default_factory=list)


class RingMatch(BaseModel):
    matched_document_id: str
    matched_filename: str
    hamming_distance: int
    matched_at: datetime
    matched_vendor: Optional[str] = None
    shared_routing: Optional[bool] = False
    shared_routing_number: Optional[str] = None


class RingResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    matches: list[RingMatch] = Field(default_factory=list)
    total_indexed: int = 0
    layout_vector: list[float] = Field(default_factory=list)
    shared_routing_matches: list[RingMatch] = Field(default_factory=list)


class AgentFinding(BaseModel):
    agent: AgentName
    status: AgentStatus
    score: float = Field(ge=0.0, le=1.0)
    headline: str
    detail: str


class VerdictResult(BaseModel):
    risk_score: int = Field(ge=0, le=100)
    recommendation: DocumentDecision
    summary: str
    findings: list[AgentFinding] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Top-level document & pipeline state
# --------------------------------------------------------------------------- #


class DocumentCreate(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    notes: Optional[str] = None


class DocumentRecord(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    perceptual_hash: str
    image_path: str
    ela_overlay_path: Optional[str] = None
    vendor: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    total_amount: Optional[float] = None
    bank_account: Optional[str] = None
    bank_routing: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    ifsc_code: Optional[str] = None
    decision: DocumentDecision = DocumentDecision.PENDING
    risk_score: Optional[int] = None
    summary: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewer_notes: Optional[str] = None


class AuditEntry(BaseModel):
    id: int
    document_id: str
    filename: str
    action: DocumentDecision
    actor: str
    notes: Optional[str] = None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Pipeline / run state
# --------------------------------------------------------------------------- #


class PipelineState(BaseModel):
    """Live state passed between agents (and streamed to the UI)."""

    document_id: str
    filename: str
    image_path: str

    extraction: Optional[ExtractionResult] = None
    forensics: Optional[ForensicsResult] = None
    policy: Optional[PolicyResult] = None
    history: Optional[HistoryResult] = None
    ring: Optional[RingResult] = None
    verdict: Optional[VerdictResult] = None

    agent_status: dict[str, AgentStatus] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)


class RunAcceptedResponse(BaseModel):
    document_id: str
    status_url: str
    stream_url: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def status_for_score(score: float, *, review_threshold: float = 0.4) -> AgentStatus:
    """Map a 0-1 risk score to a UI status badge."""
    if score >= 0.7:
        return AgentStatus.HIGH
    if score >= review_threshold:
        return AgentStatus.REVIEW
    return AgentStatus.PASSED


def recommendation_for_score(score: int) -> DocumentDecision:
    if score >= 75:
        return DocumentDecision.REJECTED
    if score >= 50:
        return DocumentDecision.ESCALATED
    return DocumentDecision.APPROVED


def risk_band_for_score(score: float) -> str:
    """Map a 0-1 fused score to the Low / Medium / High bands used by the
    reference fusion module (``medium`` starts at 0.35, ``high`` at 0.70)."""
    if score >= 0.70:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"