"""Document upload + run pipeline endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from sqlalchemy import select

from app.config import get_settings
from app.db.database import AuditRow, DocumentRow, session_factory
from app.orchestration.graph import run_pipeline
from app.schemas.models import (
    AgentStatus,
    AuditEntry,
    DocumentDecision,
    DocumentRecord,
    PipelineState,
    RunAcceptedResponse,
)
from app.services.fingerprint import perceptual_hash
from app.services.pdf_raster import looks_like_pdf, rasterize_pdf

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])


# In-memory store of live runs so the SSE endpoint can stream from the same
# coroutine that the upload endpoint launched.
_RUNS: dict[str, asyncio.Queue] = {}
_RUN_TASKS: dict[str, asyncio.Task] = {}


# --------------------------------------------------------------------------- #
# Upload + start pipeline
# --------------------------------------------------------------------------- #


@router.post("/documents", response_model=RunAcceptedResponse)
async def upload_document(file: UploadFile = File(...)) -> RunAcceptedResponse:
    settings = get_settings()
    data_dir = Path(settings.data_dir)
    uploads_dir = data_dir / "uploads"
    overlays_dir = data_dir / "overlays"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty upload")

    sha = hashlib.sha256(raw).hexdigest()
    doc_id = uuid.uuid4().hex[:12]
    safe_name = Path(file.filename or "upload.bin").name
    stored_path = uploads_dir / f"{doc_id}_{safe_name}"
    stored_path.write_bytes(raw)

    # PDF invoices can't be opened by the vision / OCR stack directly. Rasterise
    # the pages to PNG up front so the rest of the pipeline only ever sees a
    # raster image. The first page is the canonical analysis image; the original
    # PDF is kept on disk alongside it.
    working_path = stored_path
    page_count = 1
    if looks_like_pdf(stored_path, file.content_type):
        try:
            rendered = rasterize_pdf(stored_path, uploads_dir)
            if rendered:
                working_path = rendered[0]
                page_count = len(rendered)
                logger.info(
                    "rasterised PDF %s -> %d page(s); using %s",
                    stored_path.name,
                    page_count,
                    working_path.name,
                )
            else:
                raise HTTPException(
                    status_code=422,
                    detail="The PDF could not be rendered to images.",
                )
        except Exception as exc:
            logger.exception("PDF rasterisation failed")
            raise HTTPException(
                status_code=422,
                detail=f"Could not process PDF invoice: {exc}",
            ) from exc

    # Compute perceptual hash up-front so the DB row is consistent even
    # before the pipeline finishes.
    try:
        phash = perceptual_hash(working_path)
    except Exception as exc:
        logger.warning("pHash failed (%s) — storing empty hash", exc)
        phash = ""

    # After PDF rasterization, the working_path is a PNG even though the
    # upload was a PDF.  Store the *actual* image type so the /file endpoint
    # serves the correct Content-Type header.
    actual_content_type = file.content_type or "application/octet-stream"
    if working_path != stored_path:
        suffix = working_path.suffix.lower()
        if suffix == ".png":
            actual_content_type = "image/png"
        elif suffix in (".jpg", ".jpeg"):
            actual_content_type = "image/jpeg"

    async with session_factory()() as session:
        row = DocumentRow(
            id=doc_id,
            filename=safe_name,
            content_type=actual_content_type,
            size_bytes=len(raw),
            sha256=sha,
            perceptual_hash=phash,
            image_path=str(working_path),
            page_count=page_count,
            ela_overlay_path=None,
            decision=DocumentDecision.PENDING.value,
        )
        session.add(row)
        await session.commit()

    state = PipelineState(
        document_id=doc_id,
        filename=safe_name,
        image_path=str(working_path),
    )
    queue: asyncio.Queue = asyncio.Queue()
    _RUNS[doc_id] = queue
    task = asyncio.create_task(_drive_pipeline(doc_id, state, queue))
    _RUN_TASKS[doc_id] = task

    return RunAcceptedResponse(
        document_id=doc_id,
        status_url=f"/api/documents/{doc_id}",
        stream_url=f"/api/documents/{doc_id}/stream",
    )


async def _drive_pipeline(
    doc_id: str,
    state: PipelineState,
    queue: asyncio.Queue,
) -> None:
    """Run the orchestrator and push events into the SSE queue."""
    try:
        async for event in run_pipeline(state):
            await queue.put(event)
            snapshot = event.get("result") or event.get("state")
            if snapshot:
                try:
                    state = PipelineState.model_validate(snapshot)
                except Exception:
                    pass
            if event.get("event") in {"agent_done", "pipeline_done"}:
                try:
                    async with session_factory()() as session:
                        await _persist_event(session, doc_id, state, event)
                        await session.commit()
                except Exception as exc:
                    logger.warning("persist failed for doc %s: %s", doc_id, exc)
    except Exception as exc:
        logger.exception("pipeline driver failed: %s", exc)
        await queue.put({"event": "error", "agent": "pipeline", "detail": str(exc)})
        await queue.put({"event": "pipeline_done", "state": state.model_dump()})
    finally:
        # Keep queue in _RUNS briefly so late connecting SSE clients can drain final events
        await asyncio.sleep(3)
        _RUNS.pop(doc_id, None)
        _RUN_TASKS.pop(doc_id, None)


async def _persist_event(
    session,
    doc_id: str,
    state: PipelineState,
    event: dict,
) -> None:
    """Mirror in-memory state into the DB after each agent finishes."""
    if event["event"] not in {"agent_done", "pipeline_done"}:
        return
    row = (await session.execute(select(DocumentRow).where(DocumentRow.id == doc_id))).scalar_one_or_none()
    if row is None:
        return
    if state.extraction is not None:
        row.vendor = state.extraction.vendor
        row.invoice_number = state.extraction.invoice_number
        row.invoice_date = state.extraction.invoice_date
        row.total_amount = state.extraction.total_amount
        row.bank_account = state.extraction.bank_account
        row.bank_routing = state.extraction.bank_routing
    if state.forensics is not None:
        row.ela_overlay_path = state.forensics.ela_overlay_path
        row.perceptual_hash = state.forensics.perceptual_hash
    if state.ring is not None and state.ring.layout_vector:
        row.layout_vector = state.ring.layout_vector
    if state.verdict is not None:
        row.risk_score = state.verdict.risk_score
        row.summary = state.verdict.summary
    payload = {
        "extraction": state.extraction.model_dump(mode="json") if state.extraction else None,
        "forensics": state.forensics.model_dump(mode="json") if state.forensics else None,
        "policy": state.policy.model_dump(mode="json") if state.policy else None,
        "history": state.history.model_dump(mode="json") if state.history else None,
        "ring": state.ring.model_dump(mode="json") if state.ring else None,
        "verdict": state.verdict.model_dump(mode="json") if state.verdict else None,
        "agent_status": {k: (v.value if hasattr(v, "value") else str(v)) for k, v in state.agent_status.items()},
        "errors": state.errors,
    }
    row.agent_payload = payload


# --------------------------------------------------------------------------- #
# Stream (SSE)
# --------------------------------------------------------------------------- #
# ---------------------------------------------------------------------------
# Stream (SSE)
# ---------------------------------------------------------------------------

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.get("/documents/{doc_id}/stream")
async def stream_document(doc_id: str):
    queue = _RUNS.get(doc_id)

    if queue is None:
        raise HTTPException(
            status_code=404,
            detail="No active stream for this document",
        )

    async def _events():
        while True:
            event = await queue.get()

            yield _sse(event)

            if event.get("event") == "pipeline_done":
                break

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Original document file
# ---------------------------------------------------------------------------

@router.get("/documents/{doc_id}/file")
async def get_document_file(doc_id: str, download: bool = False):
    async with session_factory()() as session:
        row = (
            await session.execute(
                select(DocumentRow).where(
                    DocumentRow.id == doc_id
                )
            )
        ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="document not found",
        )

    file_path = Path(row.image_path)

    # If requested for download, prefer the original file (e.g. original PDF)
    if download:
        if file_path.name.endswith("_page1.png"):
            orig_pdf = file_path.parent / f"{file_path.stem[:-6]}.pdf"
            if orig_pdf.exists():
                return FileResponse(
                    path=str(orig_pdf),
                    media_type="application/pdf",
                    filename=row.filename,
                    content_disposition_type="attachment",
                )
        return FileResponse(
            path=str(file_path),
            media_type="application/octet-stream",
            filename=row.filename,
            content_disposition_type="attachment",
        )

    # For browser canvas preview (<img src="...">):
    # If the file path is a PDF, ensure we serve a rendered PNG image of page 1
    if file_path.suffix.lower() == ".pdf":
        page1_png = file_path.parent / f"{file_path.stem}_page1.png"
        if not page1_png.exists():
            try:
                import pymupdf
                doc = pymupdf.open(file_path)
                page = doc.load_page(0)
                pix = page.get_pixmap(dpi=150)
                pix.save(str(page1_png))
            except Exception as exc:
                logger.warning("Could not rasterize PDF on preview: %s", exc)
        if page1_png.exists():
            file_path = page1_png

    if not file_path.exists() or not file_path.is_file():
        logger.error("Document file does not exist: %s", file_path)
        raise HTTPException(
            status_code=404,
            detail="document file not found",
        )

    _MIME_MAP = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
    }
    actual_type = _MIME_MAP.get(
        file_path.suffix.lower(),
        "image/png",
    )

    return FileResponse(
        path=str(file_path),
        media_type=actual_type,
        filename=file_path.name,
        content_disposition_type="inline",
    )

# --------------------------------------------------------------------------- #
# Document detail / list / audit
# --------------------------------------------------------------------------- #


@router.get("/documents/{doc_id}", response_model=DocumentRecord)
async def get_document(doc_id: str) -> DocumentRecord:
    async with session_factory()() as session:
        row = (await session.execute(select(DocumentRow).where(DocumentRow.id == doc_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        return _row_to_record(row)


@router.get("/documents", response_model=list[DocumentRecord])
async def list_documents(limit: int = 50, offset: int = 0) -> list[DocumentRecord]:
    async with session_factory()() as session:
        stmt = select(DocumentRow).order_by(DocumentRow.created_at.desc()).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]


@router.get("/documents/{doc_id}/payload")
async def get_document_payload(doc_id: str):
    async with session_factory()() as session:
        row = (await session.execute(select(DocumentRow).where(DocumentRow.id == doc_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        if row.agent_payload is None:
            return JSONResponse({"status": "pending", "payload": None})
        return JSONResponse({"status": "ready", "payload": row.agent_payload})


# --------------------------------------------------------------------------- #
# Decisions / audit log
# --------------------------------------------------------------------------- #


@router.post("/documents/{doc_id}/decision")
async def post_decision(doc_id: str, body: dict):
    action = body.get("action")
    actor = body.get("actor", "demo-user")
    notes = body.get("notes")

    try:
        decision = DocumentDecision(action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid action: {action}") from exc

    async with session_factory()() as session:
        row = (await session.execute(select(DocumentRow).where(DocumentRow.id == doc_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        row.decision = decision.value
        row.reviewed_at = datetime.now(timezone.utc)
        row.reviewer_notes = notes
        audit = AuditRow(
            document_id=doc_id,
            filename=row.filename,
            action=decision.value,
            actor=actor,
            notes=notes,
        )
        session.add(audit)
        await session.commit()
        await session.refresh(audit)
        return {"ok": True, "audit": AuditEntry(
            id=audit.id,
            document_id=audit.document_id,
            filename=audit.filename,
            action=DocumentDecision(audit.action),
            actor=audit.actor,
            notes=audit.notes,
            created_at=audit.created_at,
        ).model_dump(mode="json")}


@router.get("/audit", response_model=list[AuditEntry])
async def list_audit(limit: int = 100):
    async with session_factory()() as session:
        stmt = select(AuditRow).order_by(AuditRow.created_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            AuditEntry(
                id=r.id,
                document_id=r.document_id,
                filename=r.filename,
                action=DocumentDecision(r.action),
                actor=r.actor,
                notes=r.notes,
                created_at=r.created_at,
            )
            for r in rows
        ]


@router.get("/rings")
async def list_rings(limit: int = 50):
    """List all indexed documents — used by the Ring-Detection view."""
    async with session_factory()() as session:
        stmt = (
            select(DocumentRow.id, DocumentRow.filename, DocumentRow.vendor, DocumentRow.created_at, DocumentRow.perceptual_hash)
            .order_by(DocumentRow.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        return [
            {
                "id": r.id,
                "filename": r.filename,
                "vendor": r.vendor,
                "created_at": r.created_at.isoformat(),
                "perceptual_hash": r.perceptual_hash,
            }
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _row_to_record(row: DocumentRow) -> DocumentRecord:
    return DocumentRecord(
        id=row.id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        perceptual_hash=row.perceptual_hash,
        image_path=row.image_path,
        ela_overlay_path=row.ela_overlay_path,
        vendor=row.vendor,
        invoice_number=row.invoice_number,
        invoice_date=row.invoice_date,
        total_amount=row.total_amount,
        bank_account=row.bank_account,
        bank_routing=row.bank_routing,
        decision=DocumentDecision(row.decision),
        risk_score=row.risk_score,
        summary=row.summary,
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
        reviewer_notes=row.reviewer_notes,
    )