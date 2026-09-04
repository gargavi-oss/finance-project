"""Schema completeness and business-rule validation of an extracted record.

Reference implementation
------------------------
The AWS guidance
``aws-solutions-library-samples/guidance-for-fraud-detection-with-intelligent-document-processing-on-aws``
does not rely on the computer-vision model alone. Before any ML runs, an Amazon
Bedrock Data Automation **blueprint** performs "automated completeness and
validity checks": the document is verified against an expected schema — are the
required fields present, extractable, and internally consistent — so that an
incomplete or self-contradictory packet is flagged before deeper review.

This module is the invoice equivalent of that blueprint. It produces two of the
six fusion signals:

``semantic_conflict_score``
    How far the extracted text departs from what a valid invoice must look
    like: missing mandatory fields, line items that do not add up to the stated
    total, arithmetic errors inside a line, impossible dates, duplicates.

``extraction_confidence_score``
    How reliably the text was read at all. The reference implementation
    "penalizes blur, artifacts, and low-resolution regions", so we blend the
    OCR engine's own confidence with a Laplacian-variance sharpness measure of
    the source image — a blurred scan can produce confident nonsense.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from app.schemas.models import (
    CompletenessIssue,
    CompletenessResult,
    ExtractionResult,
    Severity,
)

logger = logging.getLogger(__name__)

# Fields a payable invoice must carry before any fraud logic is meaningful.
REQUIRED_FIELDS = (
    "vendor",
    "invoice_number",
    "invoice_date",
    "total_amount",
)

# Absolute + relative tolerance when comparing a line-item sum to the total.
_AMOUNT_TOLERANCE = 0.02
_RELATIVE_TOLERANCE = 0.01

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%Y/%m/%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
)

# Laplacian variance considered "perfectly sharp"; typical clean scans exceed
# this, while a re-photographed or heavily compressed scan falls well below.
_SHARPNESS_SATURATION = 500.0


def _parse_date(value: str) -> datetime | None:
    candidate = re.sub(r"\s+", " ", (value or "").strip())
    if not candidate:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def measure_sharpness(image_path: str | Path) -> float:
    """Laplacian-variance sharpness of the source image, normalised to 0..1."""
    try:
        gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return 0.5
        # Cap the working resolution — sharpness is a global statistic.
        height, width = gray.shape[:2]
        scale = min(1.0, 1600.0 / max(height, width))
        if scale < 1.0:
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return float(np.clip(variance / _SHARPNESS_SATURATION, 0.0, 1.0))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("sharpness measurement failed: %s", exc)
        return 0.5


def validate_record(
    extraction: ExtractionResult,
    *,
    image_path: str | Path | None = None,
) -> CompletenessResult:
    """Validate an extracted invoice against the expected schema and arithmetic."""
    issues: list[CompletenessIssue] = []
    conflict = 0.0

    # --- required fields --------------------------------------------------- #
    present = 0
    for field in REQUIRED_FIELDS:
        value = getattr(extraction, field, None)
        if value not in (None, "", 0):
            present += 1
            continue
        issues.append(
            CompletenessIssue(
                code=f"missing_{field}",
                label=f"Missing {field.replace('_', ' ')}",
                severity=Severity.MEDIUM,
                detail=(
                    f"A payable invoice must carry a {field.replace('_', ' ')}; "
                    "it could not be extracted from this document."
                ),
                score=0.5,
            )
        )
        conflict += 0.15

    if not extraction.line_items:
        issues.append(
            CompletenessIssue(
                code="missing_line_items",
                label="No line items extracted",
                severity=Severity.MEDIUM,
                detail="The document carries a total but no itemised breakdown.",
                score=0.5,
            )
        )
        conflict += 0.12

    total = len(REQUIRED_FIELDS)
    completeness = round(present / total, 4) if total else 1.0

    # --- arithmetic: line items vs stated total ---------------------------- #
    line_sum = sum(float(item.amount or 0.0) for item in extraction.line_items)
    stated_total = extraction.total_amount

    if extraction.line_items and stated_total is not None:
        deviation = abs(line_sum - float(stated_total))
        tolerance = max(_AMOUNT_TOLERANCE, abs(float(stated_total)) * _RELATIVE_TOLERANCE)
        if deviation > tolerance:
            relative = deviation / max(abs(float(stated_total)), 1e-6)
            severity = Severity.HIGH if relative >= 0.05 else Severity.MEDIUM
            issues.append(
                CompletenessIssue(
                    code="total_mismatch",
                    label="Line items do not reconcile to the total",
                    severity=severity,
                    detail=(
                        f"Line items sum to {line_sum:.2f} but the document states "
                        f"{float(stated_total):.2f} — a gap of {deviation:.2f} "
                        f"({relative * 100:.1f}%)."
                    ),
                    score=min(1.0, 0.45 + relative),
                )
            )
            conflict += min(0.55, 0.30 + relative)

    # --- arithmetic inside each line --------------------------------------- #
    per_line_errors = 0
    for index, item in enumerate(extraction.line_items):
        expected = float(item.quantity or 0.0) * float(item.unit_price or 0.0)
        if expected > 0 and abs(expected - float(item.amount or 0.0)) > max(
            0.02, expected * 0.01
        ):
            per_line_errors += 1
    if per_line_errors:
        issues.append(
            CompletenessIssue(
                code="line_arithmetic",
                label="Line arithmetic does not hold",
                severity=Severity.MEDIUM,
                detail=(
                    f"{per_line_errors} line item(s) where quantity x unit price "
                    "does not equal the stated amount."
                ),
                score=0.4,
            )
        )
        conflict += 0.18

    # --- date sanity -------------------------------------------------------- #
    if extraction.invoice_date:
        parsed = _parse_date(extraction.invoice_date)
        if parsed is None:
            issues.append(
                CompletenessIssue(
                    code="unparseable_date",
                    label="Invoice date is not a recognised date",
                    severity=Severity.LOW,
                    detail=f"Could not parse {extraction.invoice_date!r} as a date.",
                    score=0.25,
                )
            )
            conflict += 0.10
        else:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if parsed > now:
                issues.append(
                    CompletenessIssue(
                        code="future_date",
                        label="Invoice is dated in the future",
                        severity=Severity.HIGH,
                        detail=f"Invoice date {parsed.date()} is after today.",
                        score=0.7,
                    )
                )
                conflict += 0.40
            elif (now - parsed).days > 3 * 365:
                issues.append(
                    CompletenessIssue(
                        code="stale_date",
                        label="Invoice is more than three years old",
                        severity=Severity.MEDIUM,
                        detail=f"Invoice date {parsed.date()} is unusually old for reimbursement.",
                        score=0.35,
                    )
                )
                conflict += 0.20

    # --- total sanity ------------------------------------------------------- #
    if stated_total is not None and float(stated_total) <= 0:
        issues.append(
            CompletenessIssue(
                code="non_positive_total",
                label="Total is zero or negative",
                severity=Severity.HIGH,
                detail=f"Stated total is {float(stated_total):.2f}.",
                score=0.6,
            )
        )
        conflict += 0.35

    # --- duplicate line descriptions ---------------------------------------- #
    descriptions = [
        (item.description or "").strip().lower()
        for item in extraction.line_items
        if (item.description or "").strip()
    ]
    if descriptions and len(descriptions) != len(set(descriptions)):
        issues.append(
            CompletenessIssue(
                code="duplicate_lines",
                label="Duplicate line items",
                severity=Severity.LOW,
                detail="The same description appears on more than one line.",
                score=0.25,
            )
        )
        conflict += 0.10

    # --- extraction confidence ---------------------------------------------- #
    sharpness = measure_sharpness(image_path) if image_path else 0.5
    engine_confidence = float(np.clip(extraction.confidence, 0.0, 1.0))
    extraction_confidence = float(
        np.clip(engine_confidence * (0.45 + 0.55 * sharpness), 0.0, 1.0)
    )

    if sharpness < 0.25:
        issues.append(
            CompletenessIssue(
                code="low_sharpness",
                label="Source image is soft or low resolution",
                severity=Severity.LOW,
                detail=(
                    "Laplacian sharpness is low, so extracted values are less "
                    "trustworthy than the OCR confidence suggests."
                ),
                score=0.25,
            )
        )

    return CompletenessResult(
        required_total=total,
        required_present=present,
        completeness=completeness,
        issues=issues,
        semantic_conflict_score=round(float(np.clip(conflict, 0.0, 1.0)), 4),
        extraction_confidence_score=round(extraction_confidence, 4),
        sharpness=round(float(sharpness), 4),
    )
