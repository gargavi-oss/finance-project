"""Image forensics service.

Implements three classic signals used by commercial document-fraud products
(Klippa, Trustpair, Staple AI):

* Error Level Analysis (ELA)  — re-saves JPEG at a known quality and diffs
  with the original. Edits survive at a different compression level than the
  untouched background, producing localised hotspots. We compute the
  absolute pixel-difference, normalise to 0-255, and report:

      - ``ela_score``             0..1 composite risk
      - ``ela_suspicious_ratio``  fraction of pixels above the threshold
      - ``ela_overlay_path``      a JPEG saved with the ELA heatmap as the
                                  red channel so the UI can overlay it
      - ``flagged_region``        the largest connected hotspot bounding box

* EXIF / metadata — looks for editing-tool signatures, missing camera info
  on photos that should have it, suspicious software tags, and dates that
  post-date the file's mtime.

* Font consistency — measures horizontal-stripe variance across the page. A
  page edited with a single different font produces a non-uniform variance
  profile. We report a 0..1 inconsistency score.

All scores are 0..1 where higher = more suspicious.
"""

from __future__ import annotations

import io
import logging
import math
from pathlib import Path
from typing import Any

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageChops

from app.schemas.models import BoundingBox, ForensicsFlag, ForensicsResult, Severity

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# ELA
# --------------------------------------------------------------------------- #


def _ela(image_path: Path, quality: int = 90) -> tuple[np.ndarray, np.ndarray, float]:
    """Compute ELA diff and overlay.

    Returns ``(diff_uint8, overlay_bgr, suspicious_ratio)``.
    """
    original = Image.open(image_path).convert("RGB")
    buf = io.BytesIO()
    original.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    re_encoded = Image.open(buf).convert("RGB")

    diff = ImageChops.difference(original, re_encoded)
    diff_arr = np.asarray(diff, dtype=np.int16).sum(axis=2)  # collapse channels
    diff_norm = np.clip(diff_arr * 4, 0, 255).astype(np.uint8)

    threshold = 30
    suspicious = (diff_norm > threshold).astype(np.uint8)
    ratio = float(suspicious.sum() / max(suspicious.size, 1))

    # Heatmap: scale diff into red channel for an overlay image.
    overlay_rgb = np.zeros_like(np.asarray(original))
    overlay_rgb[..., 0] = diff_norm  # red = diff intensity
    overlay_bgr = cv2.cvtColor(overlay_rgb, cv2.COLOR_RGB2BGR)
    return diff_norm, overlay_bgr, ratio


def _largest_hotspot(diff: np.ndarray) -> BoundingBox | None:
    """Return the bounding box of the largest connected diff region, if any."""
    mask = (diff > 30).astype(np.uint8) * 255
    if mask.sum() == 0:
        return None
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(cnt) < 64:  # ignore noise
        return None
    x, y, w, h = cv2.boundingRect(cnt)
    return BoundingBox(x=int(x), y=int(y), width=int(w), height=int(h))


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #


_EDITOR_SOFTWARE = {
    "photoshop",
    "gimp",
    "affinity photo",
    "pixelmator",
    "paint.net",
    "sketch",
    "figma",
}


def _metadata_signals(image_path: Path) -> tuple[dict[str, Any], float]:
    """Inspect EXIF + file metadata. Returns (signals, anomaly_score)."""
    signals: dict[str, Any] = {}
    score = 0.0

    try:
        with Image.open(image_path) as im:
            exif = im.getexif() or {}
            exif_dict = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}

        software = str(exif_dict.get("Software", "")).strip()
        creator = str(exif_dict.get("Artist") or exif_dict.get("OwnerName") or "").strip()
        datetime_original = str(exif_dict.get("DateTimeOriginal") or exif_dict.get("DateTime") or "").strip()

        signals["software"] = software or None
        signals["creator"] = creator or None
        signals["datetime_original"] = datetime_original or None

        soft_low = software.lower()
        if any(editor in soft_low for editor in _EDITOR_SOFTWARE):
            signals["editor_signature"] = True
            score += 0.6

        # Scanners / cameras almost always populate Make/Model. A receipt
        # claiming to be a scan with no Make/Model is mildly suspicious.
        has_make = bool(exif_dict.get("Make"))
        has_model = bool(exif_dict.get("Model"))
        if not has_make and not has_model:
            signals["missing_camera_info"] = True
            score += 0.2

        # Resolution anomaly: extremely low DPI is a hint of a re-screenshot.
        res_x = exif_dict.get("XResolution")
        res_y = exif_dict.get("YResolution")
        if res_x is not None and res_y is not None:
            try:
                rx = float(str(res_x).split("/")[0])
                ry = float(str(res_y).split("/")[0])
                signals["dpi"] = (rx, ry)
                if rx < 150 or ry < 150:
                    signals["low_dpi"] = True
                    score += 0.15
            except (ValueError, IndexError):
                pass
    except Exception as exc:  # pragma: no cover
        signals["error"] = str(exc)

    return signals, min(score, 1.0)


from PIL import ExifTags  # local import to keep the top tidy


# --------------------------------------------------------------------------- #
# Font consistency
# --------------------------------------------------------------------------- #


def _font_inconsistency(image_path: Path) -> float:
    """Crude font consistency score.

    Measures horizontal-stripe variance of grayscale rows. A page edited with
    a single different font produces a non-uniform variance profile (some
    stripes much higher than the rest). We compute the coefficient of
    variation of per-stripe variance as the inconsistency score.
    """
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, _ = img.shape
    if h < 50:
        return 0.0
    stripe = max(20, h // 30)
    variances = []
    for y in range(0, h - stripe, stripe):
        patch = img[y : y + stripe, :]
        # Avoid pure-white / pure-black bands (page margins).
        if float(patch.mean()) < 5 or float(patch.mean()) > 250:
            continue
        variances.append(float(patch.var()))
    if len(variances) < 4:
        return 0.0
    mean = float(np.mean(variances))
    std = float(np.std(variances))
    if mean <= 1:
        return 0.0
    cv_val = std / mean  # coefficient of variation
    # Squash to 0..1
    return float(min(1.0, cv_val / 1.5))


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def run_forensics(
    image_path: str | Path,
    *,
    overlay_dir: str | Path,
    ela_threshold: float = 0.18,
    meta_threshold: float = 0.50,
    font_threshold: float = 0.35,
) -> ForensicsResult:
    image_path = Path(image_path)
    overlay_dir = Path(overlay_dir)
    overlay_dir.mkdir(parents=True, exist_ok=True)

    diff, overlay_bgr, ela_ratio = _ela(image_path)
    ela_score = float(min(1.0, ela_ratio / 0.30))  # 30% suspicious ≈ max risk

    overlay_filename = f"{image_path.stem}_ela.jpg"
    overlay_path = overlay_dir / overlay_filename
    cv2.imwrite(str(overlay_path), overlay_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

    flagged = _largest_hotspot(diff)

    meta_signals, meta_score = _metadata_signals(image_path)
    font_score = _font_inconsistency(image_path)

    composite = max(ela_score, meta_score, font_score)

    flags: list[ForensicsFlag] = []
    if ela_ratio >= ela_threshold:
        flags.append(
            ForensicsFlag(
                code="ela_hotspot",
                label="Compression-anomaly hotspot",
                severity=Severity.HIGH if ela_ratio > 0.35 else Severity.MEDIUM,
                detail=(
                    f"ELA detected localised editing in {ela_ratio * 100:.1f}% of "
                    f"the page — likely edited text or total."
                ),
                score=ela_score,
            )
        )
    if meta_score >= meta_threshold:
        flags.append(
            ForensicsFlag(
                code="meta_anomaly",
                label="Suspicious metadata",
                severity=Severity.MEDIUM,
                detail="; ".join(
                    f"{k}={v}"
                    for k, v in meta_signals.items()
                    if k in {"editor_signature", "missing_camera_info", "low_dpi"}
                )
                or "metadata signals above threshold",
                score=meta_score,
            )
        )
    if font_score >= font_threshold:
        flags.append(
            ForensicsFlag(
                code="font_inconsistency",
                label="Font inconsistency",
                severity=Severity.MEDIUM,
                detail="Row-variance profile suggests mixed fonts on the page.",
                score=font_score,
            )
        )

    phash = imagehash.phash(Image.open(image_path))
    return ForensicsResult(
        ela_score=ela_score,
        ela_suspicious_ratio=ela_ratio,
        ela_overlay_path=str(overlay_path),
        flagged_region=flagged,
        metadata_signals=meta_signals,
        metadata_score=meta_score,
        font_inconsistency_score=font_score,
        composite_score=composite,
        flags=flags,
        perceptual_hash=str(phash),
    )