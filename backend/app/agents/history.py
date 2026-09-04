"""History Agent — flags statistically unusual vendor patterns."""

from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.db.database import session_factory
from app.schemas.models import AgentName, AgentStatus, PipelineState
from app.services.vendor_history import vendor_history

logger = logging.getLogger(__name__)


class HistoryAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(AgentName.HISTORY)

    async def run(self, state: PipelineState) -> PipelineState:
        self._mark(state, AgentStatus.RUNNING)
        if state.extraction is None:
            state.errors[self.name.value] = "extraction missing"
            self._mark(state, AgentStatus.ERROR)
            return state
        try:
            async with session_factory()() as session:
                result = await vendor_history(session, state.extraction)
            state.history = result
            if result.score >= 0.7:
                self._mark(state, AgentStatus.HIGH)
            elif result.score >= 0.3:
                self._mark(state, AgentStatus.REVIEW)
            else:
                self._mark(state, AgentStatus.CLEAR)
        except Exception as exc:
            logger.exception("history failed")
            state.errors[self.name.value] = str(exc)
            self._mark(state, AgentStatus.ERROR)
        return state