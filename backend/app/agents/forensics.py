"""Forensics Agent — the six-signal multimodal tamper model.

Runs the pipeline ported from
``Aathi-27/multimodal-document-tampering-detection``: ELA, saliency
localisation, Monte-Carlo-style uncertainty, OCR semantic conflict, OCR
extraction confidence and spatial field overlap, fused with the reference
weights. Classic EXIF and font-consistency heuristics run alongside and act as
independent overrides.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import BaseAgent
from app.config import get_settings
from app.schemas.models import AgentName, AgentStatus, PipelineState
from app.services.forensics import run_forensics

logger = logging.getLogger(__name__)


class ForensicsAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(AgentName.FORENSICS)

    async def run(self, state: PipelineState) -> PipelineState:
        self._mark(state, AgentStatus.RUNNING)
        try:
            settings = get_settings()
            overlay_dir = Path(settings.data_dir) / "overlays"
            result = run_forensics(
                state.image_path,
                overlay_dir=overlay_dir,
                ela_threshold=settings.ela_tamper_threshold,
                meta_threshold=settings.meta_anomaly_threshold,
                font_threshold=settings.font_inconsistency_threshold,
                # Supplies the two OCR-derived fusion signals.
                extraction=state.extraction,
            )
            state.forensics = result
            if result.composite_score >= 0.7:
                self._mark(state, AgentStatus.HIGH)
            elif result.composite_score >= 0.4:
                self._mark(state, AgentStatus.REVIEW)
            else:
                self._mark(state, AgentStatus.PASSED)
        except Exception as exc:
            logger.exception("forensics failed")
            state.errors[self.name.value] = str(exc)
            self._mark(state, AgentStatus.ERROR)
        return state