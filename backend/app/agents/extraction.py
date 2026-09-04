"""Extraction Agent — OCR pulls structured fields."""

from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.schemas.models import AgentName, AgentStatus, PipelineState
from app.services.ocr import run_ocr

logger = logging.getLogger(__name__)


class ExtractionAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(AgentName.EXTRACTION)

    async def run(self, state: PipelineState) -> PipelineState:
        self._mark(state, AgentStatus.RUNNING)
        try:
            result = run_ocr(state.image_path)
            state.extraction = result
            self._mark(state, AgentStatus.PASSED if result.vendor else AgentStatus.REVIEW)
        except Exception as exc:
            logger.exception("extraction failed")
            state.errors[self.name.value] = str(exc)
            self._mark(state, AgentStatus.ERROR)
        return state