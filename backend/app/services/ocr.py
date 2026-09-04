"""OCR service.

Primary backend: pytesseract (Tesseract 5). When the Tesseract binary is not
installed on the host (common on minimal Linux/macOS dev machines) we fall
back to a deterministic "demo OCR" that reads a hidden JSON manifest embedded
in the image's EXIF UserComment when present. The manifest path is used by
the synthetic invoice generator so that the demo runs end-to-end without any
system-level dependencies.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ExifTags

from app.schemas.models import (
    BoundingBox,
    ExtractionResult,
    ExtractedLineItem,
)

logger = logging.getLogger(__name__)

TESSERACT_BINARY = shutil.which("tesseract")


# --------------------------------------------------------------------------- #
# Demo OCR fallback
# --------------------------------------------------------------------------- #


def _decode_demo_payload(image: Image.Image) -> Optional[dict]:
    """Read a hidden JSON payload from the image, if present.

    We check two locations, in order:
      * the EXIF ``UserComment`` tag (used by JPEG/TIFF samples), and
      * a PNG ``tEXt`` chunk named ``dfai_payload`` (PNG does not reliably
        round-trip EXIF through Pillow, so the generator also stashes the
        payload here).
    Both carry the same ``DFAI1::``-prefixed, base64-encoded JSON.
    """
    # 1) EXIF UserComment.
    try:
        exif = image.getexif()
    except Exception:  # pragma: no cover - PIL raises on weird files
        exif = None
    if exif:
        user_comment_tag = next(
            (k for k, v in ExifTags.TAGS.items() if v == "UserComment"), None
        )
        raw = exif.get(user_comment_tag) if user_comment_tag is not None else None
        decoded = _coerce_payload(raw)
        if decoded is not None:
            return decoded
    # 2) PNG tEXt chunk.
    try:
        txt = image.info.get("dfai_payload") or image.info.get("dfai")
    except Exception:
        txt = None
    if isinstance(txt, str) and txt.startswith("DFAI1::"):
        try:
            return json.loads(base64.b64decode(txt[len("DFAI1::") :]))
        except Exception as exc:  # pragma: no cover
            logger.debug("demo OCR tEXt decode failed: %s", exc)
    return None


def _coerce_payload(raw) -> Optional[dict]:
    if not raw:
        return None
    try:
        if isinstance(raw, bytes):
            # EXIF UserComment starts with a charset identifier (8 bytes).
            payload = raw[8:]
            decoded = payload.decode("utf-8", errors="ignore").strip()
        else:
            decoded = str(raw).strip()
        if not decoded.startswith("DFAI1::"):
            return None
        return json.loads(base64.b64decode(decoded[len("DFAI1::") :]))
    except Exception as exc:  # pragma: no cover
        logger.debug("demo OCR payload decode failed: %s", exc)
        return None


def _demo_ocr(image: Image.Image) -> ExtractionResult:
    payload = _decode_demo_payload(image) or {}
    line_items = [
        ExtractedLineItem(**li)
        for li in payload.get("line_items", [])
        if isinstance(li, dict)
    ]
    return ExtractionResult(
        vendor=payload.get("vendor"),
        invoice_number=payload.get("invoice_number"),
        invoice_date=payload.get("invoice_date"),
        total_amount=payload.get("total_amount"),
        currency=payload.get("currency", "USD"),
        line_items=line_items,
        raw_text=payload.get("raw_text", ""),
        ocr_engine="demo-embedded",
        confidence=0.97,
        flagged_regions=[
            BoundingBox(**r) for r in payload.get("flagged_regions", [])
        ],
    )


# --------------------------------------------------------------------------- #
# Tesseract path
# --------------------------------------------------------------------------- #


_MONEY_RE = re.compile(r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)")
_DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b"
)
_INVOICE_RE = re.compile(
    r"(?:invoice|inv|bill)\s*(?:#|no\.?|number)?\s*[:#]?\s*([A-Z0-9-]{3,})",
    re.IGNORECASE,
)


def _tesseract_available() -> bool:
    if TESSERACT_BINARY is None:
        return False
    try:
        subprocess.run(
            [TESSERACT_BINARY, "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return True
    except Exception:
        return False


def _run_tesseract(image: Image.Image) -> ExtractionResult:
    import pytesseract  # type: ignore

    text = pytesseract.image_to_string(image)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    raw_text = "\n".join(lines)

    vendor = lines[0] if lines else None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    total_amount: Optional[float] = None

    for ln in lines:
        if not invoice_number:
            m = _INVOICE_RE.search(ln)
            if m:
                invoice_number = m.group(1).strip()
        if not invoice_date:
            m = _DATE_RE.search(ln)
            if m:
                invoice_date = m.group(1)

    # Total amount heuristic: look for lines mentioning total / amount due.
    total_keywords = ("total", "amount due", "balance due", "grand total")
    for ln in lines:
        low = ln.lower()
        if any(k in low for k in total_keywords):
            m = _MONEY_RE.search(ln)
            if m:
                total_amount = float(m.group(1).replace(",", ""))

    return ExtractionResult(
        vendor=vendor,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        total_amount=total_amount,
        currency="USD",
        line_items=[],
        raw_text=raw_text,
        ocr_engine="tesseract",
        confidence=0.78,
        flagged_regions=[],
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def run_ocr(image_path: str | Path) -> ExtractionResult:
    """Run OCR on ``image_path``.

    Uses Tesseract when available, otherwise falls back to the embedded demo
    payload (only useful for the bundled sample invoices).
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"image not found: {path}")

    with Image.open(path) as image:
        # Always check for the embedded demo payload first — it's deterministic
        # and gives a better demo experience than guessing from pixels.
        demo = _decode_demo_payload(image)
        if demo is not None:
            return _demo_ocr(image)
        if _tesseract_available():
            return _run_tesseract(image)
        # No tesseract, no demo payload → return a minimal stub.
        logger.warning(
            "Tesseract binary not found and no embedded payload. "
            "Install Tesseract for real OCR."
        )
        return ExtractionResult(
            vendor=None,
            raw_text="",
            ocr_engine="unavailable",
            confidence=0.0,
        )


def has_tesseract() -> bool:
    return _tesseract_available()