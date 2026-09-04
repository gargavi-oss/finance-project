"""Health, version, and capability endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.services.llm import LLMClient
from app.services.ocr import has_tesseract

router = APIRouter(tags=["system"])


@router.get("/health")
async def health():
    settings = get_settings()
    llm = LLMClient()
    return {
        "status": "ok",
        "version": "3.6-flash",
        "llm_provider": llm.provider,
        "llm_model": llm.model,
        "tesseract": has_tesseract(),
        "policy_path": str(settings.policy_path),
    }