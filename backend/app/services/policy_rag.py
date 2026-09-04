"""Expense-policy RAG service.

Uses TF-IDF over the policy document plus a small set of curated Q&A clauses.
This is intentionally lightweight: in a production deployment the same call
signature would talk to a real vector store (pgvector / Pinecone). For an
on-prem demo it boots instantly with no extra dependencies.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.schemas.models import (
    ExtractionResult,
    PolicyCitation,
    PolicyResult,
)

logger = logging.getLogger(__name__)


class PolicyRAG:
    """A tiny TF-IDF retriever over the company expense policy."""

    def __init__(self, policy_path: str | Path) -> None:
        self.policy_path = Path(policy_path)
        self._clauses: list[dict] = []
        self._vectorizer: Optional[TfidfVectorizer] = None
        self._matrix = None
        self.reload()

    # ---- public --------------------------------------------------------- #

    def reload(self) -> None:
        if not self.policy_path.exists():
            logger.warning("policy file missing at %s — RAG will be inert", self.policy_path)
            self._clauses = []
            self._vectorizer = None
            self._matrix = None
            return
        text = self.policy_path.read_text(encoding="utf-8")
        clauses = _split_clauses(text)
        self._clauses = clauses
        if not clauses:
            self._vectorizer = None
            self._matrix = None
            return
        corpus = [c["text"] for c in clauses]
        self._vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            stop_words="english",
            lowercase=True,
        )
        self._matrix = self._vectorizer.fit_transform(corpus)

    def check(self, extraction: ExtractionResult) -> PolicyResult:
        if not self._clauses or self._vectorizer is None or self._matrix is None:
            return PolicyResult(
                score=0.0,
                rationale="policy document unavailable",
            )

        query = _extraction_to_query(extraction)
        query_vec = self._vectorizer.transform([query])
        sims = cosine_similarity(query_vec, self._matrix).ravel()
        top_idx = list(np.argsort(-sims)[:4])

        # Also explicitly check weekend, threshold, GSTIN, and statutory TDS clauses
        for i, clause in enumerate(self._clauses):
            cid = clause.get("id", "").upper()
            if any(k in cid for k in ["EXP-001", "EXP-002", "EXP-009", "EXP-010", "EXP-011"]) and i not in top_idx:
                top_idx.append(i)

        violated: list[PolicyCitation] = []
        risk = 0.0
        rationale_bits: list[str] = []

        for idx in top_idx:
            score = float(sims[idx]) if idx < len(sims) else 0.5
            clause = self._clauses[int(idx)]
            violated_severity, reason = _violation_reason(clause, extraction)
            if violated_severity:
                snippet = clause["text"][:240]
                violated.append(
                    PolicyCitation(
                        clause_id=clause["id"],
                        clause_title=clause["title"],
                        snippet=snippet,
                        relevance=max(0.6, round(score, 3)),
                    )
                )
                risk = max(risk, _severity_to_risk(violated_severity, max(0.6, score)))
                rationale_bits.append(f"{clause['id']}: {reason}")

        rationale = "; ".join(rationale_bits) or "no policy violations detected"
        return PolicyResult(
            score=float(min(1.0, risk)),
            violated_clauses=violated,
            rationale=rationale,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


_CLAUSE_RE = re.compile(r"^(#{2,3})\s+(\[(?P<id>[A-Z0-9-]+)\]\s*)?(?P<title>.+?)\s*$", re.MULTILINE)


def _split_clauses(text: str) -> list[dict]:
    """Split a markdown document into clause chunks by ## headings."""
    chunks: list[dict] = []
    lines = text.splitlines()
    current: dict | None = None
    buffer: list[str] = []
    for line in lines:
        m = _CLAUSE_RE.match(line)
        if m and m.group(1).startswith("##"):
            if current is not None:
                current["text"] = "\n".join(buffer).strip()
                if current["text"]:
                    chunks.append(current)
            current = {
                "id": (m.group("id") or _slug(m.group("title"))).strip(),
                "title": m.group("title").strip(),
            }
            buffer = []
        else:
            if current is not None:
                buffer.append(line)
    if current is not None:
        current["text"] = "\n".join(buffer).strip()
        if current["text"]:
            chunks.append(current)
    if chunks:
        return chunks
    # Fallback: treat whole file as one clause.
    return [{"id": "POLICY", "title": "Expense Policy", "text": text.strip()}]


def _slug(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", s.upper()).strip("-")


def _extraction_to_query(ext: ExtractionResult) -> str:
    parts = [ext.vendor or "", ext.invoice_number or "", ext.invoice_date or ""]
    if ext.total_amount is not None:
        parts.append(f"amount {ext.total_amount:.2f}")
    if ext.gstin:
        parts.append(f"gstin {ext.gstin}")
    if ext.pan:
        parts.append(f"pan {ext.pan}")
    for li in ext.line_items:
        parts.append(li.description)
    return " ".join(parts)


def _violation_reason(clause: dict, ext: ExtractionResult) -> tuple[Optional[str], str]:
    """Return (severity, reason) if the extracted doc violates this clause."""
    title_low = clause["title"].lower()
    text_low = clause["text"].lower()
    cid = clause.get("id", "").upper()

    amount = ext.total_amount or 0.0

    # EXP-001: Approval thresholds
    if "auto-approval" in title_low or "approval threshold" in title_low or "EXP-001" in cid:
        if amount > 100000:
            return ("high", f"amount ₹{amount:,.2f} exceeds ₹1,00,000 INR director sign-off threshold")
        if amount > 10000:
            return ("medium", f"amount ₹{amount:,.2f} exceeds ₹10,000 INR auto-approval limit — manager review required")
        return (None, "")

    # EXP-002: GST Tax Invoice & ITC requirements
    if "gst" in title_low or "receipt" in title_low or "itc" in title_low or "EXP-002" in cid:
        if amount >= 500:
            if not ext.line_items:
                return ("medium", "no line items itemised for invoice above ₹500 INR (GST compliance requirement)")
            if not ext.gstin and not any(k in ext.raw_text.lower() for k in ["gstin", "gst no", "cgst", "sgst", "igst"]):
                return ("medium", "supplier GSTIN not detected on invoice above ₹500 INR — required for Input Tax Credit (ITC)")
        return (None, "")

    # EXP-003: Vendor PAN & GSTIN registration
    if "vendor" in title_low and ("approval" in text_low or "pan" in text_low or "gstin" in text_low or "EXP-003" in cid):
        if ext.vendor and "unknown" in ext.vendor.lower():
            return ("medium", f"vendor '{ext.vendor}' is not on approved vendor list")
        return (None, "")

    # EXP-008: Round-number anomalies
    if "duplicate" in title_low or "round-number" in title_low or "EXP-008" in cid:
        if amount >= 10000 and amount == round(amount, -3):
            return ("low", f"amount ₹{amount:,.2f} is an exact round figure — flagged for potential fabrication")
        return (None, "")

    # EXP-009: Weekend submissions
    if "weekend" in title_low or "weekend" in text_low or "EXP-009" in cid:
        if ext.invoice_date:
            try:
                from dateutil import parser as dt_parser
                dt = dt_parser.parse(ext.invoice_date, fuzzy=True)
                if dt.weekday() >= 5:  # Saturday=5, Sunday=6
                    day_name = dt.strftime("%A")
                    return ("medium", f"expense dated on weekend ({day_name}, {ext.invoice_date}) requires written business justification")
            except Exception:
                pass
        return (None, "")

    # EXP-010: Threshold spikes
    if "threshold spike" in title_low or "spike" in title_low or "EXP-010" in cid:
        if amount > 50000:
            return ("high", f"amount ₹{amount:,.2f} exceeds ₹50,000 INR departmental threshold spike limit")
        return (None, "")

    # EXP-011: Statutory TDS compliance
    if "tds" in title_low or "194" in title_low or "statutory" in title_low or "EXP-011" in cid:
        if amount > 30000:
            return ("medium", f"amount ₹{amount:,.2f} exceeds ₹30,000 INR single-bill threshold for TDS deduction under Section 194C/194J")
        return (None, "")

    return (None, "")


def _severity_to_risk(severity: str, relevance: float) -> float:
    base = {"high": 0.85, "medium": 0.55, "low": 0.25}.get(severity, 0.0)
    return base * max(0.4, relevance)