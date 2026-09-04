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
    currency: str = "USD"
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    raw_text: str = ""
    ocr_engine: str = "tesseract"
    confidence: float = 0.0
    flagged_regions: list[BoundingBox] = Field(default_factory=list)


class ForensicsFlag(BaseModel):
    code: str
    label: str
    severity: Severity
    detail: str
    score: float = Field(ge=0.0, le=1.0)


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


class PolicyCitation(BaseModel):
    clause_id: str
    clause_title: str
    snippet: str
    relevance: float = Field(ge=0.0, le=1.0)


class PolicyResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)  # higher = more policy-violating
    violated_clauses: list[PolicyCitation] = Field(default_factory=list)
    rationale: str = ""


class HistoryFlag(BaseModel):
    code: str
    label: str
    severity: Severity
    detail: str
    score: float = Field(ge=0.0, le=1.0)


class HistoryResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    vendor_prior_submissions: int = 0
    vendor_avg_amount: float = 0.0
    vendor_max_amount: float = 0.0
    vendor_stddev: float = 0.0
    flags: list[HistoryFlag] = Field(default_factory=list)


class RingMatch(BaseModel):
    matched_document_id: str
    matched_filename: str
    hamming_distance: int
    matched_at: datetime
    matched_vendor: Optional[str] = None


class RingResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    matches: list[RingMatch] = Field(default_factory=list)
    total_indexed: int = 0


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