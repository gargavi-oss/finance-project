"""Cross-document fingerprint (ring-detection) service.

Converts document layouts into 1536-dimensional perceptual vectors (compatible
with pgvector) and 64-bit perceptual hashes (pHash). Identifies when different
shell vendors are using cloned invoice templates or sharing identical bank
routing numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import imagehash
import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import DocumentRow
from app.schemas.models import ExtractionResult, RingMatch, RingResult

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 1536-Dimensional Perceptual Layout Vector (pgvector layout embeddings)
# --------------------------------------------------------------------------- #

def compute_layout_vector(image_path: str | Path) -> list[float]:
    """Compute a 1536-dimensional perceptual layout vector for pgvector indexing.

    Constructed from a 48x32 spatial density and edge gradient grid (48*32 = 1536).
    The vector captures document structure, tables, header blocks, and margins,
    invariant to minor text substitutions. Normalized to unit length (L2 norm)
    so cosine distance and euclidean distance directly measure template reuse.
    """
    path = Path(image_path)
    if not path.exists():
        return [0.0] * 1536

    try:
        if path.suffix.lower() == ".pdf":
            import pymupdf
            doc = pymupdf.open(path)
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=100)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n >= 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        else:
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)

        if img is None:
            return [0.0] * 1536

        # Resize to 48 rows x 32 columns = 1536 grid cells
        resized = cv2.resize(img, (32, 48), interpolation=cv2.INTER_AREA)

        # Invert so ink/content has high intensity and white background is 0
        content = 255.0 - resized.astype(np.float32)

        # Sobel gradients to capture structural layout lines, rules, and borders
        grad_x = cv2.Sobel(resized, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(resized, cv2.CV_32F, 0, 1, ksize=3)
        grad_mag = np.sqrt(grad_x**2 + grad_y**2)
        grad_mag_resized = cv2.resize(grad_mag, (32, 48), interpolation=cv2.INTER_AREA)

        # Combine content density (60%) and edge structure (40%)
        combined = 0.6 * content + 0.4 * grad_mag_resized
        vec = combined.flatten()

        # L2 normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return [round(float(v), 5) for v in vec]
    except Exception as exc:
        logger.warning("compute_layout_vector failed: %s", exc)
        return [0.0] * 1536


def perceptual_hash(image_path: str | Path) -> str:
    """Compute 64-bit perceptual hash (pHash), supporting images and PDFs."""
    path = Path(image_path)
    if path.suffix.lower() == ".pdf":
        try:
            import pymupdf
            doc = pymupdf.open(path)
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=100)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return str(imagehash.phash(image))
        except Exception:
            return ""
    try:
        with Image.open(path) as im:
            return str(imagehash.phash(im))
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Index and Search
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class _IndexEntry:
    document_id: str
    filename: str
    perceptual_hash: imagehash.ImageHash | None
    layout_vector: list[float] | None
    vendor: str | None
    bank_routing: str | None
    created_at: datetime


class FingerprintIndex:
    """In-memory multi-modal ring detection index, bootstrapped from the DB."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._entries: list[_IndexEntry] = []
        self._loaded = False

    async def load(self) -> None:
        if self._loaded:
            return
        stmt = select(
            DocumentRow.id,
            DocumentRow.filename,
            DocumentRow.perceptual_hash,
            DocumentRow.vendor,
            DocumentRow.bank_routing,
            DocumentRow.layout_vector,
            DocumentRow.created_at,
        )
        rows = (await self.session.execute(stmt)).all()
        for row in rows:
            ph = None
            if row.perceptual_hash:
                try:
                    ph = imagehash.hex_to_hash(row.perceptual_hash)
                except Exception:
                    ph = None
            self._entries.append(
                _IndexEntry(
                    document_id=row.id,
                    filename=row.filename,
                    perceptual_hash=ph,
                    layout_vector=row.layout_vector,
                    vendor=row.vendor,
                    bank_routing=row.bank_routing,
                    created_at=row.created_at,
                )
            )
        self._loaded = True

    def search(
        self,
        *,
        perceptual_hash_str: Optional[str] = None,
        layout_vector: Optional[list[float]] = None,
        bank_routing: Optional[str] = None,
        current_vendor: Optional[str] = None,
        exclude_id: Optional[str] = None,
        threshold: int = 8,
    ) -> tuple[list[RingMatch], list[RingMatch]]:
        target_ph = None
        if perceptual_hash_str:
            try:
                target_ph = imagehash.hex_to_hash(perceptual_hash_str)
            except Exception:
                pass

        matches: list[RingMatch] = []
        shared_routing_matches: list[RingMatch] = []
        seen_ids: set[str] = set()

        curr_vendor_norm = (current_vendor or "").strip().lower()
        curr_routing_norm = (bank_routing or "").strip().lower()

        for entry in self._entries:
            if exclude_id and entry.document_id == exclude_id:
                continue

            entry_vendor_norm = (entry.vendor or "").strip().lower()
            entry_routing_norm = (entry.bank_routing or "").strip().lower()

            # 1. Check shared bank routing numbers across different shell vendors
            if (
                curr_routing_norm
                and len(curr_routing_norm) >= 4
                and entry_routing_norm == curr_routing_norm
                and curr_vendor_norm
                and entry_vendor_norm
                and curr_vendor_norm != entry_vendor_norm
            ):
                match = RingMatch(
                    matched_document_id=entry.document_id,
                    matched_filename=entry.filename,
                    hamming_distance=0,
                    matched_at=entry.created_at,
                    matched_vendor=entry.vendor,
                    shared_routing=True,
                    shared_routing_number=entry.bank_routing,
                )
                shared_routing_matches.append(match)
                matches.append(match)
                seen_ids.add(entry.document_id)
                continue

            # 2. Check 1536-dim layout vector cosine similarity (cloned template)
            is_different_vendor = bool(
                curr_vendor_norm and entry_vendor_norm and curr_vendor_norm != entry_vendor_norm
            )
            if layout_vector and entry.layout_vector and len(entry.layout_vector) == 1536:
                cos_sim = sum(a * b for a, b in zip(layout_vector, entry.layout_vector))
                if cos_sim >= 0.92:
                    dist = max(0, int(round((1.0 - cos_sim) * 64)))
                    if is_different_vendor and entry.document_id not in seen_ids:
                        matches.append(
                            RingMatch(
                                matched_document_id=entry.document_id,
                                matched_filename=entry.filename,
                                hamming_distance=dist,
                                matched_at=entry.created_at,
                                matched_vendor=entry.vendor,
                                shared_routing=False,
                            )
                        )
                        seen_ids.add(entry.document_id)
                        continue

            # 3. Check 64-bit perceptual hash (pHash) Hamming distance
            if target_ph is not None and entry.perceptual_hash is not None:
                distance = target_ph - entry.perceptual_hash
                if distance <= threshold and is_different_vendor and entry.document_id not in seen_ids:
                    matches.append(
                        RingMatch(
                            matched_document_id=entry.document_id,
                            matched_filename=entry.filename,
                            hamming_distance=int(distance),
                            matched_at=entry.created_at,
                            matched_vendor=entry.vendor,
                            shared_routing=False,
                        )
                    )
                    seen_ids.add(entry.document_id)

        matches.sort(key=lambda m: (0 if m.shared_routing else 1, m.hamming_distance))
        return matches, shared_routing_matches

    @property
    def total_indexed(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

async def search_ring(
    session: AsyncSession,
    *,
    document_id: str,
    image_path: str | Path,
    extraction: Optional[ExtractionResult] = None,
    perceptual_hash_str: Optional[str] = None,
    threshold: int = 8,
) -> RingResult:
    """Run Ring-Detection using 1536-dim layout vectors, pHash, and bank routing matching."""
    layout_vec = compute_layout_vector(image_path)
    phash_str = perceptual_hash_str or perceptual_hash(image_path)

    routing = extraction.bank_routing if extraction else None
    vendor = extraction.vendor if extraction else None

    index = FingerprintIndex(session)
    await index.load()

    matches, shared_routing_matches = index.search(
        perceptual_hash_str=phash_str,
        layout_vector=layout_vec,
        bank_routing=routing,
        current_vendor=vendor,
        exclude_id=document_id,
        threshold=threshold,
    )

    if not matches and not shared_routing_matches:
        return RingResult(
            score=0.0,
            matches=[],
            shared_routing_matches=[],
            total_indexed=index.total_indexed,
            layout_vector=layout_vec,
        )

    # Base score computation
    closest = matches[0].hamming_distance if matches else 64
    base = max(0.0, 1.0 - closest / max(threshold, 1))
    count_bonus = min(0.2, 0.05 * (len(matches) - 1))
    score = float(min(1.0, base + count_bonus))

    # Shell vendor syndicate sharing bank routing numbers is critical risk
    if shared_routing_matches:
        score = max(score, 0.95)

    return RingResult(
        score=score,
        matches=matches,
        shared_routing_matches=shared_routing_matches,
        total_indexed=index.total_indexed,
        layout_vector=layout_vec,
    )