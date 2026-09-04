"""Patch-level spatial localisation — does the anomaly land where it matters?

Reference implementation
------------------------
``patch_localization.py`` in ``Aathi-27/multimodal-document-tampering-detection``
segments the page into patches, computes local anomaly density from the ELA and
Grad-CAM outputs, and then measures the **spatial agreement** between visual
anomaly clusters and semantically important OCR fields (signatures, amounts,
account numbers, dates) using an IoU-style overlap metric.

The insight is worth stating plainly: *where* the anomaly is matters more than
*how much* anomaly there is. A compression hotspot in a page margin is a
scanner artefact. The same hotspot sitting on the invoice total is fraud.

This module computes two scalars from the saliency map:

``density_score``
    How concentrated the anomaly is — the mean intensity of the hottest 5% of
    patches. Uniform noise scores low; one dense cluster scores high.

``overlap_score``
    How much of that anomaly lands on semantically important fields. Blends a
    precision term (fraction of anomalous patches that fall *inside* a field)
    with a per-field IoU term, weighted by how much each field matters.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from app.schemas.models import BoundingBox, PatchLocalizationResult
from app.services.ocr import field_weights, locate_semantic_fields
from app.services.saliency import concentration_stats, patch_means

logger = logging.getLogger(__name__)


def _patches_for_box(
    box: BoundingBox,
    *,
    scale_x: float,
    scale_y: float,
    map_width: int,
    map_height: int,
    rows: int,
    cols: int,
) -> set[tuple[int, int]]:
    """Convert a pixel-space bounding box into the set of patches it covers.

    ``box`` lives in *original* image coordinates. ``scale_x``/``scale_y`` map
    those onto the (possibly downscaled) saliency map, and the map is then
    divided into ``rows x cols`` patches.
    """
    if map_width <= 0 or map_height <= 0 or rows <= 0 or cols <= 0:
        return set()

    # Pixel span on the saliency map, clamped to the map bounds.
    x0 = int(max(0.0, min(map_width - 1.0, box.x * scale_x)))
    y0 = int(max(0.0, min(map_height - 1.0, box.y * scale_y)))
    x1 = int(max(0.0, min(map_width - 1.0, (box.x + box.width) * scale_x)))
    y1 = int(max(0.0, min(map_height - 1.0, (box.y + box.height) * scale_y)))

    # Patch index = floor(pixel * grid / extent).
    col0 = int(x0 * cols // map_width)
    col1 = int(x1 * cols // map_width)
    row0 = int(y0 * rows // map_height)
    row1 = int(y1 * rows // map_height)

    col0 = max(0, min(cols - 1, col0))
    col1 = max(col0, min(cols - 1, col1))
    row0 = max(0, min(rows - 1, row0))
    row1 = max(row0, min(rows - 1, row1))

    return {
        (row, col)
        for row in range(row0, row1 + 1)
        for col in range(col0, col1 + 1)
    }


def _scale_factors(image_path: str | Path, saliency_map: np.ndarray) -> tuple[float, float]:
    """Map original pixel coordinates onto the (possibly downscaled) map."""
    map_height, map_width = saliency_map.shape[:2]
    try:
        with Image.open(image_path) as handle:
            width, height = handle.size
    except Exception:  # pragma: no cover - defensive
        return 1.0, 1.0
    if not width or not height:
        return 1.0, 1.0
    return map_width / width, map_height / height


def localize_anomaly(
    image_path: str | Path,
    saliency_map: np.ndarray,
    *,
    grid: int = 16,
) -> PatchLocalizationResult:
    """Score how well the visual anomaly aligns with semantically important
    regions of the document."""
    patches = patch_means(saliency_map, grid, grid)
    rows, cols = patches.shape
    if patches.size == 0:
        return PatchLocalizationResult(grid_rows=grid, grid_cols=grid)

    flat = patches.ravel()

    # --- anomaly density -------------------------------------------------- #
    # Content-relative peak: how hot the hottest region is *compared with the
    # other regions that carry content*, not compared with blank margin. The
    # saliency map is already percentile-stretched, so its content-relative
    # peak sits near 0.5-0.9 on ordinary pages; saturating at 1.0 (the earlier
    # value) made every document read as maximally dense.
    peak, _baseline, _concentration = concentration_stats(saliency_map, grid=grid)
    density_score = float(np.clip((peak - 0.35) / 0.50, 0.0, 1.0))

    # --- anomalous patch set ---------------------------------------------- #
    median = float(np.median(flat))
    std = float(np.std(flat))
    threshold = max(0.45, median + 3.0 * std)
    anomalous = {
        (row, col)
        for row in range(rows)
        for col in range(cols)
        if patches[row, col] >= threshold
    }

    result = PatchLocalizationResult(
        grid_rows=rows,
        grid_cols=cols,
        density_score=round(density_score, 4),
        hotspot_patch_ratio=round(len(anomalous) / max(1, patches.size), 4),
        anomalous_patches=len(anomalous),
    )

    if not anomalous:
        # No anomaly anywhere — neither density nor overlap is meaningful.
        result.overlap_score = 0.0
        result.score = round(density_score * 0.5, 4)
        return result

    # --- overlap with semantic fields -------------------------------------- #
    scale_x, scale_y = _scale_factors(image_path, saliency_map)
    map_height, map_width = saliency_map.shape[:2]
    weights = field_weights()

    try:
        field_boxes = locate_semantic_fields(image_path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("field localisation unavailable: %s", exc)
        field_boxes = {}

    weighted_hits = 0.0
    total_weight = 0.0
    best_iou = 0.0
    matched: list[str] = []
    covered: set[tuple[int, int]] = set()

    for field, box in field_boxes.items():
        weight = weights.get(field, 0.5)
        field_patches = _patches_for_box(
            box,
            scale_x=scale_x,
            scale_y=scale_y,
            map_width=map_width,
            map_height=map_height,
            rows=rows,
            cols=cols,
        )
        if not field_patches:
            continue

        intersection = anomalous & field_patches
        union = anomalous | field_patches
        iou = len(intersection) / len(union) if union else 0.0
        best_iou = max(best_iou, iou * weight)

        if intersection:
            weighted_hits += weight * len(intersection)
            matched.append(field)
            covered |= field_patches

        total_weight += weight

    precision = weighted_hits / (len(anomalous) * max(total_weight, 1e-6))
    precision = float(np.clip(precision, 0.0, 1.0))
    overlap_score = float(np.clip(0.6 * precision + 0.4 * best_iou, 0.0, 1.0))

    result.overlap_score = round(overlap_score, 4)
    result.field_patches = len(covered)
    result.matched_fields = sorted(set(matched))
    result.score = round(0.5 * density_score + 0.5 * overlap_score, 4)
    return result
