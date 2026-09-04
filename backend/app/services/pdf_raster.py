"""PDF -> raster image conversion.

PDF invoices cannot be opened by Pillow / OpenCV directly, so before any
forensics, OCR or perceptual hashing runs we rasterise each page to a PNG with
PyMuPDF (``fitz``). The rest of the pipeline is written for raster images, so
after rasterisation it operates exactly as it would for an uploaded PNG/JPEG —
no other code path needs to know the source was a PDF.

The first page is used as the canonical analysis image (invoices are almost
always single page); every page is still rendered so multi-page documents are
fully extracted to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

logger: "Optional[object]"  # satisfy linters before import; replaced below
import logging

logger = logging.getLogger(__name__)

PDF_SUFFIXES = {".pdf"}


def looks_like_pdf(path: str | Path, content_type: str | None = None) -> bool:
    """Heuristic: is this upload a PDF we should rasterise?"""
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return True
    if content_type:
        return "pdf" in (content_type or "").lower()
    return False


def rasterize_pdf(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    dpi: int = 200,
) -> list[Path]:
    """Render every page of ``pdf_path`` to a PNG and return the page paths.

    Pages are named ``<stem>_page<1-based>.png`` inside ``out_dir``. Raises on
    any PyMuPDF error so the caller can decide how to surface the failure.
    """
    import pymupdf  # PyMuPDF (preferred over the deprecated `fitz` alias)

    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pages: list[Path] = []
    doc = pymupdf.open(pdf_path)
    try:
        for index, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            out = out_dir / f"{pdf_path.stem}_page{index + 1}.png"
            pix.save(str(out))
            pages.append(out)
    finally:
        doc.close()

    if not pages:
        raise ValueError("PDF contained no renderable pages")
    return pages
