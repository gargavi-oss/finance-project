"""Cross-document fingerprint (ring-detection) service.

Index every previously-processed document by its perceptual hash (pHash).
When a new document arrives, compare against the index using Hamming
distance. Anything below the configured threshold is considered a "ring
match" — fraud that repeats across documents, even under different fake
vendor names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import imagehash
import json
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import DocumentRow
from app.schemas.models import RingMatch, RingResult


@dataclass(slots=True)
class _IndexEntry:
    document_id: str
    filename: str
    perceptual_hash: imagehash.ImageHash
    vendor: str | None
    created_at: datetime


class FingerprintIndex:
    """In-memory pHash index, bootstrapped from the DB on construction."""

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
            DocumentRow.created_at,
        )
        rows = (await self.session.execute(stmt)).all()
        for row in rows:
            try:
                ph = imagehash.hex_to_hash(row.perceptual_hash)
            except Exception:
                continue
            self._entries.append(
                _IndexEntry(
                    document_id=row.id,
                    filename=row.filename,
                    perceptual_hash=ph,
                    vendor=row.vendor,
                    created_at=row.created_at,
                )
            )
        self._loaded = True

    def search(
        self,
        perceptual_hash: str,
        *,
        exclude_id: str | None = None,
        threshold: int = 8,
    ) -> list[RingMatch]:
        try:
            target = imagehash.hex_to_hash(perceptual_hash)
        except Exception:
            return []
        matches: list[RingMatch] = []
        for entry in self._entries:
            if exclude_id and entry.document_id == exclude_id:
                continue
            distance = target - entry.perceptual_hash  # hamming distance
            if distance <= threshold:
                matches.append(
                    RingMatch(
                        matched_document_id=entry.document_id,
                        matched_filename=entry.filename,
                        hamming_distance=int(distance),
                        matched_at=entry.created_at,
                        matched_vendor=entry.vendor,
                    )
                )
        matches.sort(key=lambda m: m.hamming_distance)
        return matches

    async def index(
        self,
        *,
        document_id: str,
        filename: str,
        perceptual_hash: str,
        vendor: str | None,
    ) -> None:
        try:
            ph = imagehash.hex_to_hash(perceptual_hash)
        except Exception:
            return
        self._entries.append(
            _IndexEntry(
                document_id=document_id,
                filename=filename,
                perceptual_hash=ph,
                vendor=vendor,
                created_at=datetime.now(timezone.utc),
            )
        )

    @property
    def total_indexed(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------- #
# Module-level singleton accessor (per-request)
# --------------------------------------------------------------------------- #


async def search_ring(
    session: AsyncSession,
    *,
    document_id: str,
    perceptual_hash: str,
    threshold: int,
) -> RingResult:
    index = FingerprintIndex(session)
    await index.load()
    matches = index.search(
        perceptual_hash,
        exclude_id=document_id,
        threshold=threshold,
    )
    if not matches:
        return RingResult(score=0.0, total_indexed=index.total_indexed)

    # Score: closer (smaller distance) + more matches → higher risk.
    closest = matches[0].hamming_distance
    base = max(0.0, 1.0 - closest / max(threshold, 1))
    count_bonus = min(0.2, 0.05 * (len(matches) - 1))
    score = float(min(1.0, base + count_bonus))
    return RingResult(
        score=score,
        matches=matches,
        total_indexed=index.total_indexed,
    )


def perceptual_hash(image_path: str | Path) -> str:
    with Image.open(image_path) as im:
        return str(imagehash.phash(im))