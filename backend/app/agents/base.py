"""Agent base class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.schemas.models import AgentName, AgentStatus, PipelineState

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for the six fraud-detection agents."""

    name: AgentName

    def __init__(self, name: AgentName) -> None:
        self.name = name

    @abstractmethod
    async def run(self, state: PipelineState) -> PipelineState:
        """Mutate ``state`` in place and return it."""

    def _mark(self, state: PipelineState, status: AgentStatus) -> None:
        state.agent_status[self.name.value] = status
        logger.info("[%s] %s", self.name.value, status.value)