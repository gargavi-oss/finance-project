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
                    max_retries=0,
                    timeout=6.0,
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
                    max_retries=0,
                    timeout=6.0,
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
                    max_retries=0,
                    timeout=6.0,
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
            import asyncio
            coro = self._client.ainvoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
            response = await asyncio.wait_for(coro, timeout=6.0)
            return response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.warning("LLM call failed (%s), returning demo synthesis", exc)
            return _demo_chat(system, user)
def _demo_chat(system: str, user: str) -> str:
    """Deterministic, dependency-free synthesis.

    Parses the structured prompt to extract the risk score and agent findings,
    then generates a clean, professional verdict summary without leaking any
    raw prompt text or instructions.
    """
    import re as _re

    # Extract risk score.
    risk_match = _re.search(r"(?:Risk score|Baseline score|Calibrated score):\s*(\d+)/100", user)
    risk_score = int(risk_match.group(1)) if risk_match else 50

    # Extract filename.
    file_match = _re.search(r"Document:\s*(.+?)(?:\n|$)", user)
    filename = file_match.group(1).strip() if file_match else "the submitted document"

    # Extract agent findings.
    findings: list[tuple[str, str, float]] = []
    for m in _re.finditer(
        r"- (\w+):\s*(.+?)\s*\(score=([\d.]+)\)", user
    ):
        findings.append((m.group(1), m.group(2).strip(), float(m.group(3))))

    # Extract forensic flags.
    flags: list[str] = []
    for m in _re.finditer(r"\[(?:HIGH|MEDIUM|LOW)\]\s*(.+?)(?:\n|$)", user):
        flags.append(m.group(1).strip())

    # Extract contradictions and tax reconciliation.
    contradictions: list[str] = []
    for m in _re.finditer(r"CONTRADICTION:\s*(.+?)(?:\n|$)", user):
        contradictions.append(m.group(1).strip())

    reconciled_match = _re.search(r"ARITHMETIC RECONCILED:\s*(.+?)(?:\n|$)", user)
    tax_note = reconciled_match.group(1).strip() if reconciled_match else None

    # Build a professional summary.
    parts: list[str] = []

    if risk_score >= 70:
        parts.append(
            f"The analysis of {filename} has identified material fraud indicators "
            f"requiring immediate intervention, with an AI composite risk score of {risk_score}/100."
        )
    elif risk_score >= 40:
        parts.append(
            f"The analysis of {filename} has raised concerns that warrant a focused "
            f"human review, with an AI composite risk score of {risk_score}/100."
        )
    else:
        parts.append(
            f"The analysis of {filename} indicates a low-risk submission consistent "
            f"with legitimate documentation, scoring {risk_score}/100."
        )

    # Summarise key findings.
    high_findings = [
        (name, headline) for name, headline, score in findings if score >= 0.5
    ]

    if high_findings and risk_score >= 40:
        agents_text = ", ".join(
            f"the {name} agent ({headline})" for name, headline in high_findings
        )
        parts.append(f"Key signals were raised by {agents_text}.")

    if tax_note:
        parts.append(f"Statutory tax and arithmetic reconciliation verified ({tax_note}).")

    if contradictions:
        parts.append(
            "Cross-modal analysis revealed " +
            "; ".join(c.rstrip(".") for c in contradictions[:2]) + "."
        )

    if flags and risk_score >= 40:
        flag_text = "; ".join(f.split(":")[0].strip() for f in flags[:3])
        parts.append(f"Forensic flags include: {flag_text}.")

    if not high_findings and risk_score < 40:
        parts.append(
            "All evidence agents completed their review without identifying "
            "material tampering or high-risk anomalies."
        )

    if risk_score >= 70:
        parts.append(
            "This case should be escalated for detailed manual review before "
            "any payment is authorised."
        )
    elif risk_score >= 40:
        parts.append(
            "A reviewer should examine the flagged areas before making a "
            "final disposition."
        )
    else:
        parts.append(
            "The document may proceed through the standard approval workflow."
        )

    summary_text = " ".join(parts)
    if "VERDICT_SCORE:" in user or "Format your output exactly as" in user:
        rec = "rejected" if risk_score >= 70 else ("review" if risk_score >= 40 else "approved")
        return f"VERDICT_SCORE: {risk_score}\nRECOMMENDATION: {rec}\nSUMMARY: {summary_text}"
    return summary_text