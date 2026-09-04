"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised settings. Reads from `.env` if present."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- LLM ------------------------------------------------------------
    # Gemini is the primary provider. OpenAI / Anthropic remain available as
    # optional fallbacks if a Gemini key is not configured.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-latest"

    # ---- App ------------------------------------------------------------
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- Data -----------------------------------------------------------
    data_dir: Path = Path("./data")
    policy_path: Path = Path("./data/policy/acme_expense_policy.md")
    db_url: str = "sqlite+aiosqlite:///./data/docforensic.db"
    frontend_dist: str = "../frontend/dist"

    # ---- Forensics thresholds -------------------------------------------
    ela_tamper_threshold: float = Field(0.18, ge=0.0, le=1.0)
    meta_anomaly_threshold: float = Field(0.50, ge=0.0, le=1.0)
    font_inconsistency_threshold: float = Field(0.35, ge=0.0, le=1.0)
    ring_hamming_threshold: int = Field(8, ge=0, le=64)

    # ---- Verdict weights -----------------------------------------------
    weight_forensics: float = 0.40
    weight_policy: float = 0.20
    weight_history: float = 0.15
    weight_ring: float = 0.25

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.app_cors_origins.split(",") if o.strip()]

    @property
    def has_llm(self) -> bool:
        return bool(self.gemini_api_key or self.openai_api_key or self.anthropic_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()