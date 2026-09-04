"""Weighted fusion of the six tamper signals.

Reference implementation
------------------------
``fusion.py`` in ``Aathi-27/multimodal-document-tampering-detection`` takes six
normalised scalars and combines them with fixed weights into a single
continuous fraud risk score plus a Low / Medium / High band:

============  ==============================  ======  ==========================
Signal        Source                          Weight  Role
============  ==============================  ======  ==========================
ELA           ``ela.py``                      0.25    Compression-artifact
                                                      evidence
Saliency      ``grad_cam.py``                 0.30    What the model is looking
                                                      at, and how hard
Uncertainty   ``mc_dropout.py``               0.10    How much the model trusts
                                                      itself
Semantic      ``ocr.py``                      0.15    Business-rule conflicts in
conflict                                              the extracted text
Extraction    ``ocr.py``                      0.10    How reliably the text was
confidence                                            read at all
Spatial       ``patch_localization.py``       0.10    Whether anomaly lands on
overlap                                               fields that matter
============  ==============================  ======  ==========================

Visual forensics (ELA + saliency, 0.55 combined) dominate. Semantic conflict is
the strongest independent signal. Uncertainty, extraction confidence and
spatial overlap *modulate* rather than drive the score.

We reproduce that weighting exactly, with one pragmatic addition: some signals
can be unavailable for a given document (for example when no OCR text could be
read at all). Rather than silently scoring them as zero — which would drag the
risk down and hide the gap — we renormalise over the signals that *are*
present and report the missing ones so the UI can say so out loud.
"""

from __future__ import annotations

from typing import Mapping, Optional

from app.schemas.models import (
    SignalContribution,
    SignalFusion,
    risk_band_for_score,
)

# Weights are taken verbatim from the reference fusion module.
DEFAULT_WEIGHTS: dict[str, float] = {
    "ela": 0.25,
    "saliency": 0.30,
    "uncertainty": 0.10,
    "ocr_semantic_conflict": 0.15,
    "ocr_extraction_confidence": 0.10,
    "spatial_overlap": 0.10,
}

LABELS: dict[str, str] = {
    "ela": "Compression anomaly (ELA)",
    "saliency": "Saliency localisation",
    "uncertainty": "Model uncertainty",
    "ocr_semantic_conflict": "Semantic conflict",
    "ocr_extraction_confidence": "Extraction confidence",
    "spatial_overlap": "Spatial field overlap",
}

# Signals where a *low* raw value is the suspicious outcome. Everything else is
# "higher = more suspicious".
INVERTED = {"ocr_extraction_confidence"}


def _normalise(value: float, key: str) -> float:
    """Map a raw 0..1 signal onto a 0..1 *risk* contribution."""
    clamped = min(1.0, max(0.0, float(value)))
    if key in INVERTED:
        return 1.0 - clamped
    return clamped


def fuse_signals(
    signals: Mapping[str, Optional[float]],
    *,
    weights: Optional[Mapping[str, float]] = None,
) -> SignalFusion:
    """Fuse six normalised tamper signals into a weighted risk score.

    ``signals`` maps a signal name to its raw value in [0, 1], or ``None`` when
    that signal could not be computed. Missing signals are excluded from the
    weighted sum and the remaining weights are renormalised.
    """
    table = dict(DEFAULT_WEIGHTS if weights is None else weights)

    present: dict[str, float] = {}
    missing: list[str] = []

    for key in table:
        raw = signals.get(key)
        if raw is None:
            missing.append(key)
            continue
        try:
            present[key] = _normalise(float(raw), key)
        except (TypeError, ValueError):
            missing.append(key)

    if not present:
        return SignalFusion(score=0.0, band="low", missing=missing)

    total_weight = sum(table[key] for key in present)
    if total_weight <= 0:
        return SignalFusion(score=0.0, band="low", missing=missing)

    contributions: list[SignalContribution] = []
    score = 0.0
    for key, risk in present.items():
        weight = table[key] / total_weight  # renormalised
        contribution = risk * weight
        score += contribution
        contributions.append(
            SignalContribution(
                name=key,
                label=LABELS.get(key, key),
                value=round(risk, 4),
                weight=round(weight, 4),
                contribution=round(contribution, 4),
            )
        )

    contributions.sort(key=lambda item: item.contribution, reverse=True)
    score = min(1.0, max(0.0, score))

    return SignalFusion(
        score=round(score, 4),
        band=risk_band_for_score(score),
        contributions=contributions,
        missing=missing,
    )
