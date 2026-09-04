"""Extraction Agent — OCR pulls structured fields, then validates the record.

Two stages, mirroring the AWS IDP fraud-detection guidance:

1. **Intelligent document processing** — OCR (or the embedded demo payload)
   turns pixels into a structured invoice record.
2. **Blueprint validation** — :func:`app.services.completeness.validate_record`
   checks that record against the expected invoice schema and its own
   arithmetic. This is the "automated completeness and validity check" the AWS
   reference performs before any fraud logic runs, and it feeds two of the six
   signals in the multimodal tamper model.
"""

from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.schemas.models import AgentName, AgentStatus, PipelineState
from app.services.completeness import validate_record
from app.services.ocr import run_ocr

logger = logging.getLogger(__name__)


class ExtractionAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(AgentName.EXTRACTION)

    async def run(self, state: PipelineState) -> PipelineState:
        self._mark(state, AgentStatus.RUNNING)
        try:
            result = run_ocr(state.image_path)
            # Schema + business-rule validation (AWS "blueprint" analogue).
            result.completeness = validate_record(result, image_path=state.image_path)
            state.extraction = result

            issues = result.completeness.issues if result.completeness else []
            if not result.vendor or issues:
                self._mark(state, AgentStatus.REVIEW)
            else:
                self._mark(state, AgentStatus.PASSED)
        except Exception as exc:
            logger.exception("extraction failed")
            state.errors[self.name.value] = str(exc)
            self._mark(state, AgentStatus.ERROR)
        return state