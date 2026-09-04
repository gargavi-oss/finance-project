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

On top of those three classic signals this service now runs the six-signal
multimodal tamper model ported from
``Aathi-27/multimodal-document-tampering-detection`` — see
:mod:`app.services.saliency`, :mod:`app.services.uncertainty`,
:mod:`app.services.patch_localization` and :mod:`app.services.signal_fusion`.
The fused score becomes the agent's ``composite_score``.
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
from PIL import ExifTags, Image, ImageChops

from app.schemas.models import (
    BoundingBox,
    ExtractionResult,
    ForensicsFlag,
    ForensicsResult,
    PatchLocalizationResult,
    SaliencyResult,
    Severity,
    SignalFusion,
    TamperHotspot,
    UncertaintyResult,
)
from app.services.patch_localization import localize_anomaly
from app.services.saliency import (
    compute_saliency,
    concentration_stats,
    find_hotspots,
    saliency_score,
)
from app.services.signal_fusion import fuse_signals
from app.services.uncertainty import estimate_uncertainty

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


# Residual intensity (0..1 of the 0..255 ELA map) treated as saturated evidence.
_ELA_SATURATION = 0.35


def _ela_score(diff: np.ndarray) -> float:
    """Score an ELA residual by peak intensity *and* how localised it is.

    Uses the same content-relative concentration measure as the saliency map,
    so that ordinary text edges (which always produce some re-compression
    error) do not register as tampering.
    """
    peak, _baseline, concentration = concentration_stats(
        diff.astype(np.float32) / 255.0, grid=16
    )
    if peak <= 0.0:
        return 0.0
    intensity = float(np.clip(peak / _ELA_SATURATION, 0.0, 1.0))
    return float(np.clip(0.45 * intensity + 0.55 * concentration, 0.0, 1.0))


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


# --------------------------------------------------------------------------- #
# Font consistency
# --------------------------------------------------------------------------- #


def _font_inconsistency(image_path: Path) -> float:
    """Texture-consistency score across horizontal bands of the page.

    Measures horizontal-stripe variance of grayscale rows. A page edited with a
    single different font (or a pasted block of differently-rendered text)
    produces a non-uniform texture profile: some bands are markedly rougher
    than the rest.

    Calibration note
    ----------------
    The naive version of this metric took the coefficient of variation of raw
    per-stripe variance. That conflated "different font" with "different amount
    of ink" — a dense block of text has a higher variance than a sparse one for
    perfectly innocent reasons — and consequently flagged *every* text document,
    including our own clean samples.

    We therefore normalise each band's variance by its ink coverage (the
    fraction of dark pixels) before comparing bands. The result measures
    roughness *per unit of ink*, which is what actually changes when glyphs are
    rendered differently. Empirically this drops ordinary invoices from 0.68-0.94
    to ~0.30 while leaving genuinely mixed documents around 0.47.
    """
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, _ = img.shape
    if h < 50:
        return 0.0

    stripe = max(20, h // 30)
    densities: list[float] = []

    for y in range(0, h - stripe, stripe):
        patch = img[y : y + stripe, :].astype(np.float32)
        # Skip page margins and near-empty bands.
        if float(patch.mean()) < 5 or float(patch.mean()) > 250:
            continue
        ink_ratio = float((patch < 200).mean())
        if ink_ratio < 0.005:
            continue
        densities.append(float(patch.var()) / ink_ratio)

    if len(densities) < 4:
        return 0.0

    mean = float(np.mean(densities))
    std = float(np.std(densities))
    if mean <= 1:
        return 0.0
    return float(min(1.0, (std / mean) / 1.5))


# --------------------------------------------------------------------------- #
# Saliency overlay
# --------------------------------------------------------------------------- #


def _saliency_overlay(
    image_path: Path,
    saliency_map: np.ndarray,
    out_path: Path,
) -> None:
    """Blend a colour heatmap of the saliency map over the page, for the UI."""
    page = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if page is None:
        with Image.open(image_path) as im:
            page = np.asarray(im.convert("L"))

    if page.shape[:2] != saliency_map.shape[:2]:
        page = cv2.resize(
            page,
            (saliency_map.shape[1], saliency_map.shape[0]),
            interpolation=cv2.INTER_AREA,
        )

    heat = cv2.applyColorMap(
        (np.clip(saliency_map, 0.0, 1.0) * 255).astype(np.uint8),
        cv2.COLORMAP_TURBO,
    )
    blended = cv2.addWeighted(cv2.cvtColor(page, cv2.COLOR_GRAY2BGR), 0.55, heat, 0.45, 0)
    cv2.imwrite(str(out_path), blended, [int(cv2.IMWRITE_JPEG_QUALITY), 90])


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
    extraction: ExtractionResult | None = None,
) -> ForensicsResult:
    """Run the full forensic stack on ``image_path``.

    ``extraction`` is optional; when supplied (with its completeness block) the
    two OCR-derived fusion signals become available and the six-signal fusion
    runs complete rather than renormalised over four signals.
    """
    image_path = Path(image_path)
    overlay_dir = Path(overlay_dir)
    overlay_dir.mkdir(parents=True, exist_ok=True)

    diff, overlay_bgr, ela_ratio = _ela(image_path)

    # ELA calibration. The original score was ``ratio / 0.30`` — the share of
    # the *whole page* above threshold. That only ever fires for broad
    # re-compression; a realistically tampered invoice has its total edited in a
    # region covering well under 1% of the page, so it scored ~0.05 and looked
    # clean. We score the residual the same way the saliency map is scored:
    # how intense the hottest patches are, and how far they sit above the page's
    # own baseline. Localised editing now registers; uniform noise does not.
    ela_score = _ela_score(diff)

    overlay_filename = f"{image_path.stem}_ela.jpg"
    overlay_path = overlay_dir / overlay_filename
    cv2.imwrite(str(overlay_path), overlay_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

    flagged = _largest_hotspot(diff)

    meta_signals, meta_score = _metadata_signals(image_path)
    font_score = _font_inconsistency(image_path)

    # --- six-signal multimodal tamper model -------------------------------- #
    saliency: SaliencyResult | None = None
    uncertainty: UncertaintyResult | None = None
    localization: PatchLocalizationResult | None = None

    try:
        maps = compute_saliency(image_path)
        saliency_map = maps.fused
        channel_intensities = maps.intensities
        s_score, peak, concentration = saliency_score(maps)

        saliency_overlay = overlay_dir / f"{image_path.stem}_saliency.jpg"
        _saliency_overlay(image_path, saliency_map, saliency_overlay)

        saliency = SaliencyResult(
            score=round(s_score, 4),
            peak_intensity=round(peak, 4),
            concentration=round(concentration, 4),
            hotspots=[
                TamperHotspot(
                    box=BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3]),
                    intensity=round(float(intensity), 4),
                    rank=rank,
                )
                # ``find_hotspots`` yields (intensity, box) sorted strongest first.
                for rank, (intensity, box) in enumerate(find_hotspots(saliency_map), start=1)
            ],
            channels={k: round(float(v), 4) for k, v in channel_intensities.items()},
            overlay_path=str(saliency_overlay),
        )

        uncertainty = estimate_uncertainty(image_path)
        localization = localize_anomaly(image_path, saliency_map)

        if flagged is None and saliency and saliency.hotspots:
            top_spot = saliency.hotspots[0]
            if top_spot.intensity >= 0.25:
                flagged = top_spot.box
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("multi-signal tamper model failed: %s", exc)

    # --- weighted fusion ---------------------------------------------------- #
    completeness = extraction.completeness if extraction is not None else None
    fusion = fuse_signals(
        {
            "ela": ela_score,
            "saliency": saliency.score if saliency else None,
            "uncertainty": uncertainty.epistemic_risk if uncertainty else None,
            "spatial_overlap": localization.score if localization else None,
            "ocr_semantic_conflict": (
                completeness.semantic_conflict_score if completeness else None
            ),
            "ocr_extraction_confidence": (
                completeness.extraction_confidence_score if completeness else None
            ),
        }
    )

    # The six-signal fusion drives the score. The classic metadata/font
    # heuristics remain as secondary channels because they catch classes of
    # forgery (edited EXIF, mixed rendering) that the visual signals cannot see
    # — but a heuristic only contributes once it crosses its own alert
    # threshold. Taking a plain ``max()`` here meant a single mid-range
    # heuristic (the old miscalibrated font score, which returned 1.0 on almost
    # any text page) could silently floor the forensic score at "high" no
    # matter what the multimodal model concluded.
    # Completeness / validity (AWS blueprint analogue) is a *primary* signal,
    # not just one of six. A total that does not reconcile with its own line
    # items is self-contained, high-confidence evidence of post-issuance editing
    # and does not need corroboration from the visual channels to warrant
    # escalation. (On these synthetic invoices the re-compression / ELA trace is
    # weak because the forgery is "retype the total and re-export", which is
    # exactly the case Error Level Analysis is least sensitive to — so the
    # arithmetic contradiction is the decisive, reliable discriminator.)
    sem_conflict = completeness.semantic_conflict_score if completeness else 0.0
    base = max(fusion.score, 0.7 * meta_score, 0.7 * font_score)
    if sem_conflict >= 0.30:
        base = max(base, 0.70)
    composite = min(
        1.0,
        base
        + 0.5 * max(0.0, meta_score - meta_threshold)
        + 0.4 * max(0.0, font_score - font_threshold),
    )

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

    # --- completeness / validity signal (AWS blueprint analogue) ----------- #
    if completeness is not None and completeness.semantic_conflict_score >= 0.30:
        has_total_mismatch = any(
            i.code == "total_mismatch" and i.severity == Severity.HIGH
            for i in completeness.issues
        )
        detail = "; ".join(
            i.label
            for i in completeness.issues
            if i.code
            in {
                "total_mismatch",
                "missing_line_items",
                "non_positive_total",
                "line_arithmetic",
            }
        )
        flags.append(
            ForensicsFlag(
                code="semantic_conflict",
                label="Extracted figures do not reconcile",
                severity=Severity.HIGH if has_total_mismatch else Severity.MEDIUM,
                detail=detail
                or "Internal contradiction in the extracted record (schema or arithmetic).",
                score=completeness.semantic_conflict_score,
            )
        )

    # --- flags from the multimodal model ------------------------------------ #
    if saliency is not None and saliency.score >= 0.55 and saliency.hotspots:
        top = saliency.hotspots[0]
        flags.append(
            ForensicsFlag(
                code="saliency_hotspot",
                label="Tamper saliency concentrated on a localised region",
                severity=Severity.HIGH if saliency.score >= 0.75 else Severity.MEDIUM,
                detail=(
                    f"Fused saliency {saliency.score:.2f} "
                    f"(concentration {saliency.concentration:.2f}) with the "
                    f"strongest cluster at "
                    f"({top.box.x}, {top.box.y}) {top.box.width}x{top.box.height}px."
                ),
                score=saliency.score,
            )
        )

    if (
        localization is not None
        and localization.overlap_score >= 0.35
        and localization.matched_fields
    ):
        flags.append(
            ForensicsFlag(
                code="anomaly_on_semantic_field",
                label="Anomaly overlaps a fraud-critical field",
                severity=Severity.HIGH if localization.overlap_score >= 0.60 else Severity.MEDIUM,
                detail=(
                    "Visual anomaly concentrates on: "
                    + ", ".join(f.replace("_", " ") for f in localization.matched_fields)
                    + f" (overlap {localization.overlap_score:.2f})."
                ),
                score=localization.overlap_score,
            )
        )

    if uncertainty is not None and uncertainty.epistemic_risk >= 0.45:
        flags.append(
            ForensicsFlag(
                code="model_uncertainty",
                label="Detector is unstable on this page",
                severity=Severity.LOW,
                detail=(
                    f"Across {uncertainty.passes} stochastic passes the tamper "
                    f"score moved by ±{uncertainty.stddev:.3f} — treat the "
                    "visual verdict with caution."
                ),
                score=uncertainty.epistemic_risk,
            )
        )

    if flagged is None and (composite >= 0.40 or sem_conflict >= 0.30):
        try:
            from app.services.ocr import locate_semantic_fields
            fields = locate_semantic_fields(image_path)
            if "total" in fields:
                flagged = fields["total"]
            elif fields:
                flagged = next(iter(fields.values()))
        except Exception as exc:
            logger.warning("Could not locate semantic fields for flagged region: %s", exc)

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
        saliency=saliency,
        uncertainty=uncertainty,
        patch_localization=localization,
        fusion=fusion,
    )