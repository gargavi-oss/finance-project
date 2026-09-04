"""Spatial saliency for tamper localisation.

Reference implementation
------------------------
``grad_cam.py`` in ``Aathi-27/multimodal-document-tampering-detection`` wraps an
EfficientNetB7 classifier with Grad-CAM and produces two things:

    1. a *saliency map* — which regions of the page drove the model's
       "tampered vs. clean" decision, and
    2. a *saliency score* — a scalar describing how strongly those regions
       influence the classification.

That repository ships trained TensorFlow artefacts and expects a GPU-backed
SageMaker endpoint. DocForensic AI has neither, so we keep the identical
*contract* while swapping the gradient-based explanation for a deterministic,
gradient-free **multi-residual saliency map**. Four complementary residual
channels are computed on CPU and fused:

===================  ==========================================================
Channel              What it detects
===================  ==========================================================
``ela``              Re-compression error. A spliced region re-encodes at a
                     different JPEG quality than its host page, so ELA lights
                     up exactly along the paste boundary.
``noise``            ``image - median(image)``. Spliced content carries a
                     different sensor / processing noise signature than the
                     surrounding scan.
``edge``             Local Canny edge density that departs from the page's own
                     baseline. Pasted text is usually crisper (or softer) than
                     the native rendering.
``jpeg_grid``        8x8 block-boundary discontinuity. A pasted region is
                     almost never aligned to the host JPEG grid, so energy
                     lands on non-grid coordinates.
===================  ==========================================================

The fused map answers "where", and :func:`saliency_score` answers "how much":
a page is suspicious when a *localised* region stands out sharply from the
page's own baseline — not merely when the page is globally noisy.

Calibration
-----------
An earlier version of this module normalised every channel with a per-image
2nd/98th-percentile stretch. That was a design error, and a serious one:
stretching guarantees that *every* page produces a map reaching 1.0, so a
pristine document looks exactly as suspicious as a forged one. It also makes
scores incomparable between documents, which is fatal for a risk-ranking
system.

All channels now use fixed, absolute saturation constants measured against
typical page content. A clean page produces a genuinely dark map.

The ``edge`` density channel was dropped for the same reason it was introduced
with good intentions but behaved badly: on a sparse text document — which is
what an invoice is — local edge density measures *where the ink is*, not where
the anomaly is. Every line of text registered as "inconsistent".
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Analysis is capped at this edge length; beyond it, JPEG blocks shrink below
# the resolution where block-grid reasoning is meaningful anyway.
MAX_ANALYSIS_EDGE = 1600

# Channel fusion weights. Re-compression evidence dominates, exactly as in the
# reference model where ELA + Grad-CAM carry 0.55 of the total weight.
_CHANNEL_WEIGHTS = {
    "ela": 0.45,
    "noise": 0.30,
    "jpeg_grid": 0.25,
}

# Fixed saturation constants — see the "Calibration" note in the module
# docstring. These are deliberately NOT derived from each image's percentiles.
# Values are the raw channel level that should read as "fully anomalous",
# measured against ordinary invoice content.
_CHANNEL_SATURATION = {
    "ela": 0.06,        # raw re-compression error; p99 sits near 0.02 when clean
    "noise": 0.30,      # structure-normalised high-frequency residual
    "jpeg_grid": 0.60,  # 8x8 grid disagreement, already naturally in [0, 1]
}


# --------------------------------------------------------------------------- #
# Loading / normalisation
# --------------------------------------------------------------------------- #


def load_gray(image_path: str | Path) -> np.ndarray:
    """Load ``image_path`` as a float32 grayscale array in [0, 1].

    Large pages are downscaled so the whole analysis stays sub-second on CPU.
    """
    path = Path(image_path)
    arr = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        with Image.open(path) as im:
            arr = np.asarray(im.convert("L"))

    height, width = arr.shape[:2]
    scale = min(1.0, MAX_ANALYSIS_EDGE / max(height, width))
    if scale < 1.0:
        arr = cv2.resize(
            arr,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return arr.astype(np.float32) / 255.0


def _percentile_norm(values: np.ndarray) -> np.ndarray:
    """Robustly stretch an array into [0, 1] using the 2nd/98th percentile."""
    low = float(np.percentile(values, 2))
    high = float(np.percentile(values, 98))
    if high - low < 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    return np.clip((values - low) / (high - low), 0.0, 1.0).astype(np.float32)


# --------------------------------------------------------------------------- #
# Residual channels
# --------------------------------------------------------------------------- #


def ela_residual(image_path: str | Path, quality: int = 90) -> np.ndarray:
    """Re-encode at ``quality`` and return the per-pixel absolute error."""
    with Image.open(image_path) as handle:
        original = handle.convert("RGB")

    width, height = original.size
    scale = min(1.0, MAX_ANALYSIS_EDGE / max(width, height))
    if scale < 1.0:
        original = original.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.LANCZOS,
        )

    buffer = io.BytesIO()
    original.save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    with Image.open(buffer) as re_encoded:
        re_arr = np.asarray(re_encoded.convert("RGB"), dtype=np.float32)

    diff = np.abs(np.asarray(original, dtype=np.float32) - re_arr).mean(axis=2)
    return (diff / 255.0).astype(np.float32)


def noise_residual(gray: np.ndarray) -> np.ndarray:
    """High-frequency noise left after a 3x3 median de-noising pass."""
    as_u8 = (np.clip(gray, 0.0, 1.0) * 255.0).astype(np.uint8)
    median = cv2.medianBlur(as_u8, 3).astype(np.float32) / 255.0
    return np.abs(gray - median).astype(np.float32)


def jpeg_grid_misalignment(gray: np.ndarray) -> np.ndarray:
    """Measure how poorly local gradient energy aligns to the 8x8 JPEG grid.

    On an untouched JPEG, most gradient energy sits *on* block boundaries
    (coordinates that are multiples of 8). A region pasted in from another
    image carries its own grid, so its energy lands off-grid. We tile the page
    into 8x8 windows, compare on-grid against off-grid energy, and return the
    normalised disagreement.
    """
    height, width = gray.shape[:2]
    if height < 24 or width < 24:
        return np.zeros_like(gray, dtype=np.float32)

    grad_x = np.zeros_like(gray)
    grad_x[:, 1:] = np.abs(np.diff(gray, axis=1))
    grad_y = np.zeros_like(gray)
    grad_y[1:, :] = np.abs(np.diff(gray, axis=0))

    def _axis(grad: np.ndarray, axis_length: int, horizontal: bool) -> np.ndarray:
        coords = np.arange(axis_length)
        on_mask = (coords % 8 == 0).astype(np.float32)
        if horizontal:
            # Shift by one because grad[:, 1:] is the diff between col i and i-1.
            on_grid = grad * np.roll(on_mask, 1)[None, :]
            off_grid = grad * (1.0 - np.roll(on_mask, 1))[None, :]
        else:
            on_grid = grad * np.roll(on_mask, 1)[:, None]
            off_grid = grad * (1.0 - np.roll(on_mask, 1))[:, None]

        # cv2.blur averages over the window; rescale so on/off are comparable.
        on_energy = cv2.blur(on_grid, (8, 8)) * 8.0
        off_energy = cv2.blur(off_grid, (8, 8)) * (8.0 / 7.0)
        total = on_energy + off_energy
        return np.abs(on_energy - off_energy) / (total + 1e-6)

    misalign_x = _axis(grad_x, width, horizontal=True)
    misalign_y = _axis(grad_y, height, horizontal=False)
    combined = 0.5 * (misalign_x + misalign_y)
    # Already naturally bounded in [0, 1] — do NOT percentile-stretch it.
    return cv2.blur(combined, (5, 5)).astype(np.float32)


def _resized_channel(values: np.ndarray, target: np.ndarray) -> np.ndarray:
    if values.shape == target.shape:
        return values
    return cv2.resize(
        values,
        (target.shape[1], target.shape[0]),
        interpolation=cv2.INTER_LINEAR,
    )


# --------------------------------------------------------------------------- #
# Patch helpers
# --------------------------------------------------------------------------- #


def patch_means(values: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Average ``values`` into a ``rows x cols`` grid of patch intensities."""
    height, width = values.shape[:2]
    rows = max(1, min(rows, height))
    cols = max(1, min(cols, width))

    # Trim to an exact multiple so reshape is well defined.
    trimmed = values[: rows * (height // rows), : cols * (width // cols)]
    if trimmed.size == 0:
        return np.zeros((rows, cols), dtype=np.float32)

    grid = trimmed.reshape(
        rows,
        trimmed.shape[0] // rows,
        cols,
        trimmed.shape[1] // cols,
    )
    return grid.mean(axis=(1, 3)).astype(np.float32)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


class SaliencyMaps(NamedTuple):
    """Result of :func:`compute_saliency`.

    ``fused`` is the weighted saliency map used for localisation and the UI
    overlay. ``ela`` is the re-compression channel on its own, kept separately
    because it is the only channel that directly measures compression history
    and therefore anchors the final score.
    """

    fused: np.ndarray
    ela: np.ndarray
    intensities: dict[str, float]


def compute_saliency(
    image_path: str | Path,
    *,
    quality: int = 90,
) -> SaliencyMaps:
    """Fuse the residual channels into one absolutely-calibrated saliency map."""
    gray = load_gray(image_path)
    height, width = gray.shape[:2]

    # Structure mask — where the page actually carries content.
    #
    # The noise channel responds to *any* glyph, so on a normal invoice it
    # lights up every line of text. Dividing by local structure converts it
    # from "there is ink here" into "the rendering here is anomalous given the
    # ink that is here". Blank margin is barely damped, so a splice in an empty
    # region still shows up strongly.
    ink = (gray < 0.80).astype(np.float32)
    structure = cv2.blur(ink, (25, 25))
    level = float(np.percentile(structure, 75))
    if level > 1e-6:
        structure = np.clip(structure / level, 0.0, 1.0)
    structure = structure + 0.25

    raw: dict[str, np.ndarray] = {
        # ELA is intrinsically about compression history, not ink — left raw.
        "ela": _resized_channel(ela_residual(image_path, quality), gray),
        "noise": noise_residual(gray) / structure,
        "jpeg_grid": jpeg_grid_misalignment(gray),
    }

    # Absolute calibration — every channel is scaled by a fixed constant so a
    # clean page produces a dark map rather than being stretched to fill [0, 1].
    scaled = {
        name: np.clip(values / _CHANNEL_SATURATION[name], 0.0, 1.0).astype(np.float32)
        for name, values in raw.items()
    }

    fused = np.zeros((height, width), dtype=np.float32)
    for name, weight in _CHANNEL_WEIGHTS.items():
        fused += weight * scaled[name]

    intensities = {name: float(values.mean()) for name, values in scaled.items()}
    return SaliencyMaps(fused=fused, ela=scaled["ela"], intensities=intensities)


def concentration_stats(
    values: np.ndarray,
    *,
    grid: int = 16,
    content_floor_frac: float = 0.25,
    top_fraction: float = 0.10,
) -> tuple[float, float, float]:
    """Peak intensity and outlier-ness *among content-bearing patches*.

    Returns ``(peak, baseline, concentration)``.

    Two calibration errors were fixed here, both of which made every document
    look maximally anomalous:

    1. The first version compared the hottest patches against the median patch
       over the whole page. On an invoice most patches are blank margin, so the
       median is ~0 and *any* text made the page look maximally concentrated —
       every document scored ~0.72 whether or not it had been touched.
    2. Selecting "content" as a fixed quantile (the top 40% of patches) failed
       for the same reason: an invoice that is 85% white still has plenty of
       near-zero patches inside its top 40%, so the baseline stayed near zero.

    The floor is now a fraction of the page's own 95th percentile, which is a
    *segmentation* step ("which patches carry ink?") rather than a scoring
    step. Within that population the question becomes the one we actually care
    about: does one region stand out from the other regions that look like it?
    """
    patches = patch_means(values, grid, grid)
    flat = patches.ravel()
    if flat.size < 4:
        return 0.0, 0.0, 0.0

    reference = float(np.percentile(flat, 95.0))
    floor = max(reference * content_floor_frac, 1e-6)
    content = flat[flat >= floor]
    if content.size < 4:
        return 0.0, 0.0, 0.0

    top_count = max(1, int(round(content.size * top_fraction)))
    peak = float(np.sort(content)[-top_count:].mean())
    baseline = float(np.median(content))
    concentration = float(np.clip((peak - baseline) / (peak + 1e-6), 0.0, 1.0))
    return peak, baseline, concentration


def saliency_score(
    maps: SaliencyMaps,
    *,
    grid: int = 16,
) -> tuple[float, float, float]:
    """Score a saliency result by how far one region stands out from its peers.

    The score is anchored on the ELA channel — the only channel that measures
    compression history directly — and modulated by how concentrated the fused
    map is. Anchoring matters: the fused map is inevitably busier than ELA
    alone, and scoring it directly gave every invoice a baseline of ~0.65
    regardless of whether it had been touched.

    Returns ``(score, peak_intensity, concentration)``.
    """
    ela_peak, _baseline, _ = concentration_stats(maps.ela, grid=grid)
    fused_peak, _fused_base, concentration = concentration_stats(maps.fused, grid=grid)
    if ela_peak <= 0.0 and fused_peak <= 0.0:
        return 0.0, 0.0, 0.0

    score = float(
        np.clip(
            0.5 * ela_peak + 0.3 * fused_peak + 0.2 * concentration,
            0.0,
            1.0,
        )
    )
    return score, float(fused_peak), concentration


def find_hotspots(
    saliency_map: np.ndarray,
    *,
    grid: int = 16,
    limit: int = 4,
) -> list[tuple[tuple[int, int, int, int], float]]:
    """Return up to ``limit`` bounding boxes (x, y, w, h) for the hottest
    connected patch clusters, strongest first."""
    height, width = saliency_map.shape[:2]
    patches = patch_means(saliency_map, grid, grid)

    median = float(np.median(patches))
    std = float(np.std(patches))
    threshold = max(0.45, median + 3.0 * std)

    mask = (patches >= threshold).astype(np.uint8)
    if mask.sum() == 0:
        return []

    rows, cols = patches.shape
    cell_h = max(1, height // rows)
    cell_w = max(1, width // cols)

    # cv2 works on uint8 labels; upscale the patch mask to pixel resolution.
    pixel_mask = np.kron(mask, np.ones((cell_h, cell_w), dtype=np.uint8))
    pixel_mask = pixel_mask[:height, :width]

    count, labels, stats, _ = cv2.connectedComponentsWithStats(pixel_mask, connectivity=8)
    if count <= 1:
        return []

    clusters: list[tuple[float, tuple[int, int, int, int]]] = []
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < 0.0005 * height * width:  # ignore specks
            continue
        intensity = float(saliency_map[y : y + h, x : x + w].mean())
        clusters.append((intensity, (int(x), int(y), int(w), int(h))))

    clusters.sort(key=lambda item: item[0], reverse=True)
    return clusters[:limit]
