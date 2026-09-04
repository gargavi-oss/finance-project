"""SQLAlchemy ORM models + async session helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    perceptual_hash: Mapped[str] = mapped_column(String(32), index=True)
    image_path: Mapped[str] = mapped_column(String(512))
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    ela_overlay_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bank_routing: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ifsc_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    layout_vector: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(40), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    action: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(64), default="Risk Analyst")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SessionRow(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


# ---- engine / session ----------------------------------------------------- #


_settings = get_settings()
_engine = create_async_engine(_settings.db_url, echo=False, future=True)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Backfill newly-added columns on databases created by older versions.
        # create_all() does not ALTER existing tables, so add the column
        # defensively and ignore the error if it is already present.
        for col in (
            "page_count INTEGER NOT NULL DEFAULT 1",
            "bank_account VARCHAR(64)",
            "bank_routing VARCHAR(64)",
            "gstin VARCHAR(32)",
            "pan VARCHAR(32)",
            "ifsc_code VARCHAR(32)",
            "layout_vector JSON",
        ):
            try:
                await conn.exec_driver_sql(f"ALTER TABLE documents ADD COLUMN {col}")
            except Exception:
                pass


def session_factory() -> async_sessionmaker[AsyncSession]:
    return _SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _SessionLocal() as session:
        yield session