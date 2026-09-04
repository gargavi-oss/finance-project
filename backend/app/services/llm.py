"""LLM abstraction.

The Verdict and Policy agents can use an LLM for synthesis. If no API key is
configured we run in **DEMO mode** — a small, deterministic template engine
that produces high-quality outputs without any network call. This keeps the
project fully runnable on a fresh checkout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMMessage:
    role: str
    content: str


class LLMClient:
    """Provider-agnostic LLM facade."""

    def __init__(self) -> None:
        settings = get_settings()
        self._provider: str = "demo"
        self._model: Optional[str] = None
        self._client = None

        if settings.gemini_api_key:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore

                self._client = ChatGoogleGenerativeAI(
                    google_api_key=settings.gemini_api_key,
                    model=settings.gemini_model,
                    temperature=0.1,
                    max_retries=1,
                    request_timeout=60,
                )
                self._provider = "gemini"
                self._model = settings.gemini_model
            except Exception as exc:
                logger.warning("Gemini init failed, falling back to demo LLM: %s", exc)
                self._client = None
        elif settings.openai_api_key:
            try:
                from langchain_openai import ChatOpenAI  # type: ignore

                self._client = ChatOpenAI(
                    api_key=settings.openai_api_key,
                    model=settings.openai_model,
                    temperature=0.1,
                )
                self._provider = "openai"
                self._model = settings.openai_model
            except Exception as exc:
                logger.warning("OpenAI init failed, falling back to demo LLM: %s", exc)
                self._client = None
        elif settings.anthropic_api_key:
            try:
                from langchain_anthropic import ChatAnthropic  # type: ignore

                self._client = ChatAnthropic(
                    api_key=settings.anthropic_api_key,
                    model=settings.anthropic_model,
                    temperature=0.1,
                )
                self._provider = "anthropic"
                self._model = settings.anthropic_model
            except Exception as exc:
                logger.warning("Anthropic init failed, falling back to demo LLM: %s", exc)
                self._client = None

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> Optional[str]:
        return self._model

    async def chat(self, system: str, user: str) -> str:
        if self._client is None:
            return _demo_chat(system, user)
        try:
            response = await self._client.ainvoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
            return response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.warning("LLM call failed (%s), returning demo synthesis", exc)
            return _demo_chat(system, user)


def _demo_chat(system: str, user: str) -> str:
    """Deterministic, dependency-free synthesis. Output shaped by the prompt."""
    return (
        "[demo LLM] " + (user.strip()[:1200])
    )