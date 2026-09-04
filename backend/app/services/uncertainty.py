"""Predictive uncertainty estimation for the tamper model.

Reference implementation
------------------------
``mc_dropout.py`` in ``Aathi-27/multimodal-document-tampering-detection`` keeps
dropout layers active at inference time and performs N stochastic forward
passes through the EfficientNetB7 head. The *variance across passes* is the
epistemic uncertainty: a model that disagrees with itself is a model that does
not know, and `"I do not know"` is itself a fraud signal.

We do not ship a stochastic neural network, so we apply the same estimator one
level up — at the input/encoder level instead of the weight level. Each
"stochastic forward pass" is:

    1. perturb the page with a randomised but *seeded* transform
       (sub-degree rotation, JPEG-grid shift, mild Gaussian noise), and
    2. re-encode it at a randomised JPEG quality,

then score the resulting re-compression residual. Running N passes gives a
distribution of tamper scores. A page that stays stable across perturbations is
a confident clean page; a page whose score swings wildly is one the detector
cannot make up its mind about — the same conclusion MC Dropout would reach.

Determinism
-----------
The perturbation schedule is driven by a fixed seed rather than wall-clock
entropy. Real MC Dropout is stochastic, but a fraud system that returns a
different verdict every time it is asked the same question is unusable in an
audit. We keep the estimator, and make the estimator reproducible.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import numpy as np
from PIL import Image

from app.schemas.models import UncertaintyResult
from app.services.saliency import concentration_stats

logger = logging.getLogger(__name__)

# Fixed seed — see the "Determinism" note in the module docstring.
SEED = 20260101

# Longer edge used for the perturbation passes. Uncertainty needs *relative*
# movement between passes, not absolute precision, so we can afford to shrink.
MAX_PASS_EDGE = 1100

# Raw ELA residual value (0..1) that we treat as "fully saturated" evidence.
# Empirically, clean re-encodes sit near 0.01-0.02 and spliced text near 0.06+.
_ELA_SATURATION = 0.10

# Perturbation schedule: (jpeg_quality, rotation_deg, grid_shift_px, noise_sigma)
_PASS_SCHEDULE = (
    (90, 0.0, 0, 0.0),
    (90, 0.35, 3, 0.6),
    (85, -0.30, 5, 0.0),
    (95, 0.20, 1, 1.0),
    (80, -0.45, 6, 0.4),
    (92, 0.45, 2, 0.8),
)


def _prepare(image: Image.Image) -> Image.Image:
    """Downscale to the analysis resolution used by the perturbation passes."""
    width, height = image.size
    scale = min(1.0, MAX_PASS_EDGE / max(width, height))
    if scale >= 1.0:
        return image
    return image.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.LANCZOS,
    )


def _perturb(
    image: Image.Image,
    rng: np.random.Generator,
    rotation: float,
    grid_shift: int,
    noise_sigma: float,
) -> Image.Image:
    """Apply one stochastic (but seeded) transform to the page."""
    if abs(rotation) > 1e-3:
        image = image.rotate(
            rotation,
            resample=Image.BICUBIC,
            expand=False,
            fillcolor=(255, 255, 255),
        )

    if grid_shift:
        # Shifting the crop shifts the 8x8 JPEG grid relative to the content,
        # which is exactly the axis a spliced region tends to disagree on.
        image = image.crop(
            (grid_shift, grid_shift, image.width, image.height)
        ).resize((image.width, image.height), Image.BICUBIC)

    sigma = float(noise_sigma + rng.uniform(-0.15, 0.15))
    if sigma > 0.05:
        arr = np.asarray(image, dtype=np.float32)
        arr += rng.normal(0.0, sigma, arr.shape)
        image = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")

    return image


def _pass_score(image: Image.Image, quality: int) -> float:
    """Score one stochastic re-encode: how concentrated is the ELA residual."""
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    buffer.seek(0)

    with Image.open(buffer) as re_encoded:
        re_arr = np.asarray(re_encoded.convert("RGB"), dtype=np.float32)

    diff = np.abs(np.asarray(image, dtype=np.float32) - re_arr).mean(axis=2) / 255.0
    peak, _baseline, concentration = concentration_stats(diff, grid=16)
    if peak <= 0.0:
        return 0.0

    intensity = float(np.clip(peak / _ELA_SATURATION, 0.0, 1.0))
    return float(np.clip(0.45 * intensity + 0.55 * concentration, 0.0, 1.0))


def estimate_uncertainty(
    image_path: str | Path,
    *,
    passes: int = 6,
) -> UncertaintyResult:
    """Run ``passes`` stochastic re-encodes and summarise their spread."""
    path = Path(image_path)
    scores: list[float] = []

    try:
        with Image.open(path) as handle:
            base = _prepare(handle.convert("RGB"))

        rng = np.random.default_rng(SEED)
        schedule = _PASS_SCHEDULE[: max(1, min(passes, len(_PASS_SCHEDULE)))]
        # If the caller asks for more passes than the schedule holds, cycle it.
        while len(schedule) < max(1, passes):
            schedule = schedule + _PASS_SCHEDULE

        for quality, rotation, grid_shift, noise_sigma in schedule[: max(1, passes)]:
            perturbed = _perturb(base, rng, rotation, grid_shift, noise_sigma)
            scores.append(round(_pass_score(perturbed, quality), 4))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("uncertainty estimation failed: %s", exc)
        return UncertaintyResult(
            passes=0,
            mean_score=0.0,
            stddev=0.0,
            confidence=0.0,
            epistemic_risk=1.0,
        )

    if not scores:
        return UncertaintyResult(
            passes=0,
            mean_score=0.0,
            stddev=0.0,
            confidence=0.0,
            epistemic_risk=1.0,
        )

    array = np.asarray(scores, dtype=np.float64)
    mean = float(array.mean())
    stddev = float(array.std())

    # Normalised dispersion: a stddev that is large *relative to the signal* is
    # what matters. The +0.10 floor keeps quiet pages from producing a
    # meaninglessly volatile ratio.
    dispersion = float(np.clip(stddev / (mean + 0.10), 0.0, 1.0))
    confidence = float(round(1.0 - dispersion, 4))

    return UncertaintyResult(
        passes=len(scores),
        mean_score=round(mean, 4),
        stddev=round(stddev, 4),
        confidence=confidence,
        epistemic_risk=round(1.0 - confidence, 4),
        per_pass=scores,
    )
