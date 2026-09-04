"""Ring-Detection Agent — cross-document fingerprint matching."""

from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.config import get_settings
from app.db.database import session_factory
from app.schemas.models import AgentName, AgentStatus, PipelineState
from app.services.fingerprint import search_ring

logger = logging.getLogger(__name__)


class RingDetectionAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(AgentName.RING)

    async def run(self, state: PipelineState) -> PipelineState:
        self._mark(state, AgentStatus.RUNNING)
        if state.forensics is None:
            state.errors[self.name.value] = "forensics missing"
            self._mark(state, AgentStatus.ERROR)
            return state
        try:
            settings = get_settings()
            async with session_factory()() as session:
                result = await search_ring(
                    session,
                    document_id=state.document_id,
                    perceptual_hash=state.forensics.perceptual_hash,
                    threshold=settings.ring_hamming_threshold,
                )
            state.ring = result
            if result.score >= 0.6:
                self._mark(state, AgentStatus.HIGH)
            elif result.score >= 0.2:
                self._mark(state, AgentStatus.REVIEW)
            else:
                self._mark(state, AgentStatus.CLEAR)
        except Exception as exc:
            logger.exception("ring detection failed")
            state.errors[self.name.value] = str(exc)
            self._mark(state, AgentStatus.ERROR)
        return state