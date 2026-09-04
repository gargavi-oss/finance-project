"""Policy Agent — RAG-checks the submission against company policy."""

from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import BaseAgent
from app.config import get_settings
from app.schemas.models import AgentName, AgentStatus, PipelineState
from app.services.policy_rag import PolicyRAG

logger = logging.getLogger(__name__)


class PolicyAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(AgentName.POLICY)
        settings = get_settings()
        self._rag = PolicyRAG(settings.policy_path)

    async def run(self, state: PipelineState) -> PipelineState:
        self._mark(state, AgentStatus.RUNNING)
        if state.extraction is None:
            state.errors[self.name.value] = "extraction missing"
            self._mark(state, AgentStatus.ERROR)
            return state
        try:
            result = self._rag.check(state.extraction)
            state.policy = result
            if result.score >= 0.7:
                self._mark(state, AgentStatus.HIGH)
            elif result.score >= 0.3:
                self._mark(state, AgentStatus.REVIEW)
            else:
                self._mark(state, AgentStatus.CLEAR)
        except Exception as exc:
            logger.exception("policy failed")
            state.errors[self.name.value] = str(exc)
            self._mark(state, AgentStatus.ERROR)
        return state