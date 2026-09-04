"""Generate synthetic invoice images for the demo.

Produces three PNGs in ``data/invoices``:

1. ``clean_invoice.png`` — fully genuine invoice, passes forensics.
2. ``tampered_invoice.png`` — total edited after scan (ELA should catch).
3. ``ring_invoice_a.png`` / ``ring_invoice_b.png`` — same physical
   template with different fake vendor names (Ring-Detection should
   fingerprint-match them.

Each image carries a hidden JSON payload in its EXIF UserComment so the
``services.ocr`` module can return deterministic, realistic text without
requiring the Tesseract binary.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

logger = logging.getLogger(__name__)

# Compression history of a generated invoice. SCAN_QUALITY models the capture
# device, EXPORT_QUALITY the file the vendor actually emails you.
SCAN_QUALITY = 75
EXPORT_QUALITY = 92


def _load_font(size: int = 22) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_invoice(
    *,
    out_path: Path,
    vendor: str,
    invoice_number: str,
    invoice_date: str,
    line_items: list[tuple[str, int, float, float]],
    total: float,
    tampering_seed: int | None = None,
    base_jpeg: bytes | None = None,
    theme: str = "default",
) -> bytes:
    """Render a JPEG invoice and save a PNG copy.

    If ``base_jpeg`` is provided the invoice is built *on top of* that existing
    image (only the vendor name + invoice number are overlaid). This is how the
    ring-fraud pair stays pixel-near-identical so their perceptual hashes match.
    Returns the JPEG bytes of the rendered invoice so callers can reuse it as a
    base for a variant.

    ``theme`` selects a distinct visual template so genuinely different vendors
    produce different perceptual hashes (otherwise every near-blank white page
    would ring-match every other page).
    """
    W, H = 1240, 1754
    title_font = _load_font(48)
    header_font = _load_font(28)
    body_font = _load_font(24)
    small_font = _load_font(20)
    mono_total_font = _load_font(34)

    if base_jpeg is None:
        img = Image.new("RGB", (W, H), "white")
    else:
        img = Image.open(io.BytesIO(base_jpeg)).convert("RGB")
    draw = ImageDraw.Draw(img)

    if base_jpeg is None:
        # Theme-specific large structural elements so pHash differs per vendor.
        accent = (180, 35, 24)
        if theme == "clean":
            accent = (34, 120, 94)
            # Full-height light sidebar band — a large distinguishing region.
            draw.rectangle([(0, 0), (170, H)], fill=(226, 232, 229))
            draw.rectangle([(0, 0), (170, 200)], fill=accent)
        elif theme == "tampered":
            accent = (40, 82, 170)
            # Big logo block top-right.
            draw.rectangle([(W - 430, 30), (W - 30, 250)], fill=accent)
            draw.text((W - 410, 110), "CLOUD", fill=(255, 255, 255), font=header_font)
            draw.text((W - 410, 150), "HOST", fill=(255, 255, 255), font=header_font)
        elif theme == "ring":
            accent = (150, 30, 30)
            # Central watermark panel + footer band (shared by the ring pair).
            draw.rectangle([(300, 560), (940, 1180)], fill=(244, 236, 236))
            draw.rectangle([(0, H - 90), (W, H)], fill=accent)
        else:
            draw.rectangle([(0, 0), (W, 14)], fill=accent)

        # Letterhead bar (top).
        draw.rectangle([(0, 0), (W, 14)], fill=accent)
        draw.text((60, 50), vendor, fill=(20, 20, 20), font=title_font)
        draw.text((60, 120), "INVOICE", fill=(80, 80, 80), font=header_font)
        draw.text((60, 200), f"Invoice #: {invoice_number}", fill=(40, 40, 40), font=body_font)
        draw.text((60, 240), f"Date:    {invoice_date}", fill=(40, 40, 40), font=body_font)
        y = 320
        draw.line([(60, y - 10), (W - 60, y - 10)], fill=accent, width=2)
        cols = [(60, "Description"), (760, "Qty"), (880, "Unit"), (1040, "Amount")]
        for x, label in cols:
            draw.text((x, y), label, fill=(40, 40, 40), font=body_font)
        y += 40
        draw.line([(60, y - 10), (W - 60, y - 10)], fill=(220, 220, 220), width=1)
        for desc, qty, unit, amount in line_items:
            draw.text((cols[0][0], y), desc, fill=(20, 20, 20), font=body_font)
            draw.text((cols[1][0], y), str(qty), fill=(20, 20, 20), font=body_font)
            draw.text((cols[2][0], y), f"${unit:,.2f}", fill=(20, 20, 20), font=body_font)
            draw.text((cols[3][0], y), f"${amount:,.2f}", fill=(20, 20, 20), font=body_font)
            y += 40
        y += 40
        draw.line([(760, y - 10), (W - 60, y - 10)], fill=(220, 220, 220), width=1)
        draw.text((760, y + 10), "Total Due:", fill=(40, 40, 40), font=body_font)
        draw.text((1040, y + 10), f"${total:,.2f}", fill=accent, font=mono_total_font)
        draw.text((60, H - 180), "Remit to: Acme Corp Finance, 123 Market St, Springfield", fill=(120, 120, 120), font=small_font)
        draw.text((60, H - 140), "Payment terms: Net 30. Late fees of 1.5%/mo apply.", fill=(120, 120, 120), font=small_font)
    else:
        # Variant render: overlay only the vendor name + invoice number onto the
        # shared template, so the two ring invoices stay pixel-near-identical.
        draw.rectangle([(55, 44), (1000, 112)], fill=(255, 255, 255))
        draw.text((60, 50), vendor, fill=(20, 20, 20), font=title_font)
        draw.rectangle([(55, 196), (680, 238)], fill=(255, 255, 255))
        draw.text((60, 200), f"Invoice #: {invoice_number}", fill=(40, 40, 40), font=body_font)

    # Encode as JPEG. SCAN_QUALITY simulates the capture device (a phone photo
    # or a flatbed scan); EXPORT_QUALITY is what the file is finally stored at.
    # The two-stage history is what gives Error Level Analysis something to
    # measure, and it is how real invoices end up looking.
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=SCAN_QUALITY)
    jpeg_bytes = buf.getvalue()

    img_out = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")

    # The total the document actually *shows*. For a tampered invoice this is
    # the edited figure — the value OCR would read off the page, and the value
    # that therefore no longer reconciles with the line items.
    printed_total = total

    if tampering_seed is not None:
        rng = random.Random(tampering_seed)
        printed_total = total + rng.randint(8000, 24000)

        # Blank the entire total row — rule, label and figure — and redraw it.
        #
        # The surrounding page has already been through one JPEG encode at
        # SCAN_QUALITY. The replacement glyphs have not. Re-saving at the higher
        # EXPORT_QUALITY therefore leaves the pasted band with a genuinely
        # different compression history than its host, which is precisely the
        # artefact Error Level Analysis detects. (The previous implementation
        # re-saved the whole page at a *lower* quality instead, re-compressing
        # everything uniformly and leaving no localised trace at all.)
        draw2 = ImageDraw.Draw(img_out)
        draw2.rectangle([(740, y - 16), (W - 60, y + 62)], fill=(255, 255, 255))
        draw2.line([(760, y - 10), (W - 60, y - 10)], fill=(220, 220, 220), width=1)
        draw2.text((760, y + 10), "Total Due:", fill=(40, 40, 40), font=body_font)
        draw2.text(
            (1040, y + 10),
            f"${printed_total:,.2f}",
            fill=accent,
            font=mono_total_font,
        )

    # Final export.
    buf2 = io.BytesIO()
    img_out.save(buf2, "JPEG", quality=EXPORT_QUALITY)
    final_jpeg = buf2.getvalue()
    img_out = Image.open(io.BytesIO(final_jpeg)).convert("RGB")

    # Embed the demo payload in EXIF. This stands in for OCR output, so it must
    # report what is printed on the page — not what the invoice originally said.
    payload = {
        "vendor": vendor,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "total_amount": printed_total,
        "currency": "USD",
        "line_items": [
            {"description": d, "quantity": q, "unit_price": u, "amount": a}
            for d, q, u, a in line_items
        ],
        "raw_text": (
            f"{vendor}\nINVOICE {invoice_number}\nDate {invoice_date}\n"
            f"Total ${printed_total:,.2f}"
        ),
    }
    exif_bytes = _build_exif_with_payload(payload)
    # PNG does not reliably round-trip EXIF via PIL, so we also stash the demo
    # payload in a PNG tEXt chunk (robust) and keep the EXIF blob for JPEG use.
    payload_str = "DFAI1::" + base64.b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("ascii")
    meta = PngInfo()
    meta.add_text("dfai_payload", payload_str)
    img_out.save(out_path, "PNG", exif=exif_bytes, pnginfo=meta)
    logger.info("wrote %s", out_path)
    # Return the *final* export so a ring variant can be drawn on top of the
    # exact image the first vendor submitted.
    return final_jpeg


def _build_exif_with_payload(payload: dict) -> bytes:
    """Build a minimal EXIF block with a UserComment that holds the demo payload."""
    import piexif  # type: ignore

    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    comment = b"DFAI1::" + base64.b64encode(
        json.dumps(payload).encode("utf-8")
    )
    exif_dict["Exif"][piexif.ExifIFD.UserComment] = comment
    return piexif.dump(exif_dict)


def payload_bytes(payload: dict) -> bytes:
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return b"DFAI1::" + encoded.encode("ascii")


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def main(out_dir: str | Path = "./data/invoices") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---- 1. Clean invoice --------------------------------------------- #
    _draw_invoice(
        out_path=out / "INV-2026-00181.png",
        vendor="Office Supplies Co.",
        invoice_number="INV-2026-00181",
        invoice_date="2026-08-04",
        line_items=[
            ("Printer paper, 5 reams", 5, 8.99, 44.95),
            ("Toner cartridge HP-58X", 1, 89.50, 89.50),
            ("Stapler, heavy duty", 2, 14.75, 29.50),
        ],
        total=163.95,
        theme="clean",
    )

    # ---- 2. Tampered invoice (total edited) --------------------------- #
    _draw_invoice(
        out_path=out / "INV-2026-04182.png",
        vendor="Cloud Hosting Co.",
        invoice_number="INV-2026-04182",
        invoice_date="2026-08-22",
        line_items=[
            ("cPanel licensing — 1 year", 1, 250.00, 250.00),
            ("Enterprise security subscription", 1, 1200.00, 1200.00),
            ("Backup solution add-on", 1, 95.00, 95.00),
        ],
        total=1545.00,
        tampering_seed=42,
        theme="tampered",
    )

    # ---- 3. Ring-fraud pair (same template, different vendors) -------- #
    ring_items = [
        ("Office chairs (×4)", 4, 215.00, 860.00),
        ("Standing desk", 1, 540.00, 540.00),
        ("Monitor arm", 2, 92.50, 185.00),
    ]
    # Render alpha fully, then reuse its JPEG bytes as the beta template so the
    # two invoices share a near-identical pixel fingerprint (ring-match).
    alpha_jpeg = _draw_invoice(
        out_path=out / "ring_alpha_supplies.png",
        vendor="Alpha Supplies LLC",
        invoice_number="INV-2026-05201",
        invoice_date="2026-08-29",
        line_items=ring_items,
        total=1585.00,
        theme="ring",
    )
    _draw_invoice(
        out_path=out / "ring_beta_office.png",
        vendor="Beta Office Goods",
        invoice_number="INV-2026-07833",
        invoice_date="2026-08-30",
        line_items=ring_items,
        total=1585.00,
        base_jpeg=alpha_jpeg,
    )

    # Touch the policy file so the file exists even before first run.
    print("Sample invoices written to", out.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="./data/invoices")
    args = parser.parse_args()
    main(args.out)