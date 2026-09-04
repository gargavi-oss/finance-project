"""Vendor-history service.

Performs robust vendor entity resolution (via GSTIN, PAN, normalized name, or bank account),
tracks historical submission ledgers, lifetime spend, and flags statistical anomalies:
sudden amount spikes, duplicate invoice numbers, frequency anomalies, and BEC bank account changes.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import DocumentRow
from app.schemas.models import (
    ExtractionResult,
    HistoryFlag,
    HistoryResult,
    PriorInvoiceSummary,
    Severity,
)

logger = logging.getLogger(__name__)


def _normalize_vendor_name(name: str | None) -> str:
    """Normalize vendor name for robust matching across OCR variations."""
    if not name:
        return ""
    # Strip common business entity suffixes
    clean = re.sub(
        r"(?i)\b(ltd|pvt|limited|private|works|co|corp|corporation|inc|llp|enterprises|electric|solutions)\b",
        "",
        name,
    )
    # Retain only alphanumeric characters
    return re.sub(r"[^a-z0-9]", "", clean.lower())


def _canonical_gstin(gstin: str | None) -> str:
    """Normalize Indian GSTIN to canonical form, correcting common OCR character swaps."""
    if not gstin:
        return ""
    g = re.sub(r"[^A-Za-z0-9]", "", gstin).upper()
    if len(g) >= 2:
        # First 2 chars are state code digits (e.g. 06 for Haryana): correct OCR O->0, I->1
        d0 = "0" if g[0] == "O" else ("1" if g[0] in ("I", "L") else g[0])
        d1 = "0" if g[1] == "O" else ("1" if g[1] in ("I", "L") else g[1])
        g = d0 + d1 + g[2:]
    return g


def _canonical_pan(pan: str | None) -> str:
    """Normalize Indian PAN to canonical form, correcting common OCR character swaps."""
    if not pan:
        return ""
    p = re.sub(r"[^A-Za-z0-9]", "", pan).upper()
    if len(p) == 10:
        # Digits 5 to 9 (indices 5..8) are numbers: correct OCR O->0, I->1
        digits = "".join(
            "0" if c == "O" else ("1" if c in ("I", "L") else c)
            for c in p[5:9]
        )
        return p[:5] + digits + p[9:]
    return p


def _mask_account(acc: str | None) -> str:
    """Mask bank account number for privacy."""
    if not acc:
        return ""
    clean = acc.strip()
    if len(clean) <= 4:
        return clean
    return f"••••{clean[-4:]}"


async def _fetch_historical_rows(
    session: AsyncSession,
    extraction: ExtractionResult,
    document_id: Optional[str] = None,
) -> list[DocumentRow]:
    """Retrieve prior submissions for this vendor using multi-attribute resolution."""
    stmt = (
        select(DocumentRow)
        .order_by(DocumentRow.created_at.desc())
        .limit(300)
    )
    if document_id:
        stmt = stmt.where(DocumentRow.id != document_id)

    all_rows: Iterable[DocumentRow] = (await session.execute(stmt)).scalars().all()

    curr_vendor_norm = _normalize_vendor_name(extraction.vendor)
    curr_raw_vendor = (extraction.vendor or "").strip().lower()
    curr_gstin = _canonical_gstin(extraction.gstin)
    curr_pan = _canonical_pan(extraction.pan)
    curr_bank = (extraction.bank_account or "").strip()

    matched: list[DocumentRow] = []
    seen_ids: set[str] = set()

    for r in all_rows:
        if r.id in seen_ids:
            continue

        row_gstin = _canonical_gstin(r.gstin)
        row_pan = _canonical_pan(r.pan)
        row_vendor_norm = _normalize_vendor_name(r.vendor)
        row_raw_vendor = (r.vendor or "").strip().lower()
        row_bank = (r.bank_account or "").strip()

        # 1. Match by statutory GSTIN (highest confidence)
        if curr_gstin and row_gstin and len(curr_gstin) >= 10 and curr_gstin == row_gstin:
            matched.append(r)
            seen_ids.add(r.id)
            continue

        # 2. Match by PAN
        if curr_pan and row_pan and len(curr_pan) == 10 and curr_pan == row_pan:
            matched.append(r)
            seen_ids.add(r.id)
            continue

        # 3. Match by normalized name
        if curr_vendor_norm and row_vendor_norm:
            if curr_vendor_norm == row_vendor_norm:
                matched.append(r)
                seen_ids.add(r.id)
                continue
            if (len(curr_vendor_norm) >= 5 and len(row_vendor_norm) >= 5) and (
                curr_vendor_norm in row_vendor_norm or row_vendor_norm in curr_vendor_norm
            ):
                matched.append(r)
                seen_ids.add(r.id)
                continue

        # 4. Match by raw vendor name
        if curr_raw_vendor and row_raw_vendor and curr_raw_vendor == row_raw_vendor:
            matched.append(r)
            seen_ids.add(r.id)
            continue

        # 5. Match by designated bank account if >= 6 digits
        if curr_bank and row_bank and len(curr_bank) >= 6 and curr_bank == row_bank:
            matched.append(r)
            seen_ids.add(r.id)
            continue

    return matched


async def vendor_history(
    session: AsyncSession,
    extraction: ExtractionResult,
    document_id: Optional[str] = None,
) -> HistoryResult:
    """Inspect vendor history and flag statistical and identity anomalies."""
    vendor = (extraction.vendor or "").strip()
    amount = float(extraction.total_amount or 0.0)
    has_tax_id = bool(getattr(extraction, "gstin", None) or getattr(extraction, "pan", None))

    if not vendor and not has_tax_id:
        return HistoryResult(score=0.0, flags=[])

    matched = await _fetch_historical_rows(session, extraction, document_id=document_id)
    n = len(matched)

    if n == 0:
        score = 0.15 if has_tax_id else (0.30 if amount > 10000 else 0.20)
        sev = Severity.LOW if has_tax_id else (Severity.MEDIUM if amount > 10000 else Severity.LOW)
        detail = (
            f"First submission for '{vendor or 'unnamed vendor'}' (statutory GSTIN/PAN verified)."
            if has_tax_id
            else f"First submission for '{vendor or 'unnamed vendor'}' (no prior records)."
        )
        return HistoryResult(
            score=score,
            vendor_prior_submissions=0,
            trust_status="new",
            flags=[
                HistoryFlag(
                    code="unknown_vendor",
                    label="First-time vendor",
                    severity=sev,
                    detail=detail,
                    score=score,
                )
            ],
        )

    amounts = [float(r.total_amount or 0.0) for r in matched]
    total_spend = sum(amounts)
    avg = total_spend / n
    var = sum((a - avg) ** 2 for a in amounts) / n
    std = math.sqrt(var)
    mx = max(amounts)
    mn = min(amounts)

    first_seen = min(r.created_at for r in matched).strftime("%b %d, %Y")
    last_seen = max(r.created_at for r in matched).strftime("%b %d, %Y")

    # Itemized prior invoices ledger (latest 10)
    prior_invoices: list[PriorInvoiceSummary] = []
    for r in matched[:10]:
        prior_invoices.append(
            PriorInvoiceSummary(
                document_id=r.id,
                filename=r.filename,
                invoice_number=r.invoice_number,
                invoice_date=r.invoice_date,
                total_amount=round(float(r.total_amount or 0.0), 2),
                decision=r.decision or "pending",
                risk_score=r.risk_score,
                created_at=r.created_at.strftime("%b %d, %Y") if r.created_at else "",
                bank_account_masked=_mask_account(r.bank_account),
                ifsc_code=r.ifsc_code or r.bank_routing,
                gstin=r.gstin,
            )
        )

    # Known banking and tax coordinates on record
    known_accounts = list(dict.fromkeys(_mask_account(r.bank_account) for r in matched if r.bank_account))
    known_ifscs = list(dict.fromkeys(r.ifsc_code or r.bank_routing for r in matched if (r.ifsc_code or r.bank_routing)))

    flags: list[HistoryFlag] = []
    score = 0.0

    # 1. Duplicate invoice number detection
    curr_inv_no = (extraction.invoice_number or "").strip()
    if curr_inv_no and len(curr_inv_no) >= 2 and curr_inv_no.lower() not in ("date", "n/a", "none"):
        dup = next((r for r in matched if (r.invoice_number or "").strip().lower() == curr_inv_no.lower()), None)
        if dup:
            dup_date = dup.created_at.strftime("%b %d, %Y") if dup.created_at else "prior date"
            flags.append(
                HistoryFlag(
                    code="duplicate_invoice",
                    label="Duplicate invoice number",
                    severity=Severity.HIGH,
                    detail=(
                        f"Invoice number '{curr_inv_no}' was already submitted on {dup_date} "
                        f"for ₹{float(dup.total_amount or 0.0):,.2f} INR (Filename: {dup.filename}). "
                        "Duplicate submission or re-billing detected."
                    ),
                    score=0.92,
                )
            )
            score = max(score, 0.92)

    # 2. Sudden bank account change detection (Vendor Intelligence / BEC Protection)
    curr_bank_account = (extraction.bank_account or "").strip()
    curr_bank_routing = (extraction.ifsc_code or extraction.bank_routing or "").strip().upper()

    if curr_bank_account:
        prev_accounts = [r.bank_account.strip() for r in matched if r.bank_account and r.bank_account.strip()]
        if prev_accounts and curr_bank_account not in prev_accounts:
            prev_acc = prev_accounts[0]
            masked_prev = _mask_account(prev_acc)
            masked_curr = _mask_account(curr_bank_account)
            flags.append(
                HistoryFlag(
                    code="bank_account_changed",
                    label="Sudden bank account change",
                    severity=Severity.HIGH,
                    detail=(
                        f"Vendor '{vendor}' historically requested payment to bank account {masked_prev}, "
                        f"but this submission specifies a new account {masked_curr}. "
                        "Sudden bank account changes on established vendors indicate high risk of "
                        "vendor email compromise (BEC) or payment redirection fraud."
                    ),
                    score=0.88,
                )
            )
            score = max(score, 0.88)

    if curr_bank_routing:
        prev_routings = [
            (r.ifsc_code or r.bank_routing).strip().upper()
            for r in matched
            if (r.ifsc_code or r.bank_routing) and (r.ifsc_code or r.bank_routing).strip()
        ]
        if prev_routings and curr_bank_routing not in prev_routings:
            flags.append(
                HistoryFlag(
                    code="bank_routing_changed",
                    label="Bank IFSC routing code changed",
                    severity=Severity.HIGH,
                    detail=(
                        f"Vendor '{vendor}' bank IFSC routing changed from {prev_routings[0]} "
                        f"to {curr_bank_routing}. Verify with vendor via out-of-band communication."
                    ),
                    score=0.80,
                )
            )
            score = max(score, 0.80)

    # 3. Statutory GSTIN discrepancy
    curr_gstin = _canonical_gstin(extraction.gstin)
    if curr_gstin:
        prev_gstins = [_canonical_gstin(r.gstin) for r in matched if r.gstin and r.gstin.strip()]
        if prev_gstins and curr_gstin not in prev_gstins:
            flags.append(
                HistoryFlag(
                    code="gstin_mismatch",
                    label="GSTIN tax identity mismatch",
                    severity=Severity.HIGH,
                    detail=(
                        f"Vendor '{vendor}' historically operated under GSTIN '{prev_gstins[0]}', "
                        f"but this claim cites '{curr_gstin}'. Indicates potential legal entity masquerading."
                    ),
                    score=0.85,
                )
            )
            score = max(score, 0.85)

    # 4. Amount spike: amount > avg + 2*std and amount > 1.8 * avg
    if std > 0 and amount > avg + 2 * std and amount > 1.8 * avg:
        flags.append(
            HistoryFlag(
                code="amount_spike",
                label="Amount spike",
                severity=Severity.HIGH,
                detail=(
                    f"₹{amount:,.2f} INR is {amount / max(avg, 1):.1f}× the vendor's "
                    f"historical average of ₹{avg:,.2f} INR (n={n}, std=₹{std:,.2f} INR)."
                ),
                score=0.80,
            )
        )
        score = max(score, 0.80)

    # 5. Single unusually-large submission
    if amount > 1.5 * mx and amount > 10000:
        flags.append(
            HistoryFlag(
                code="largest_submission",
                label="Largest-ever submission",
                severity=Severity.MEDIUM,
                detail=(
                    f"Amount ₹{amount:,.2f} INR is {(amount / max(mx, 1)):.1f}× the "
                    f"vendor's previous max of ₹{mx:,.2f} INR."
                ),
                score=0.55,
            )
        )
        score = max(score, 0.55)

    # 6. Frequency velocity spike
    if n >= 3:
        last_3 = amounts[:3]
        if all(a > 0 for a in last_3) and amount > 1.4 * (sum(last_3) / 3):
            flags.append(
                HistoryFlag(
                    code="frequency_spike",
                    label="Recent frequency spike",
                    severity=Severity.MEDIUM,
                    detail=(
                        f"3 most-recent submissions for this vendor average "
                        f"₹{sum(last_3) / 3:,.2f} INR; this claim is ₹{amount:,.2f} INR."
                    ),
                    score=0.50,
                )
            )
            score = max(score, 0.50)

    # 7. Trust status and clean profile reward
    has_high_risk = any(f.severity == Severity.HIGH for f in flags)
    if has_high_risk:
        trust_status = "flagged"
    elif n >= 2:
        trust_status = "verified"
        if not flags:
            flags.append(
                HistoryFlag(
                    code="verified_vendor_profile",
                    label="Verified vendor profile",
                    severity=Severity.LOW,
                    detail=(
                        f"{n} prior submissions on record totaling ₹{total_spend:,.2f} INR. "
                        "Banking coordinates and statutory tax IDs match established profile."
                    ),
                    score=0.05,
                )
            )
            score = 0.05
    else:
        trust_status = "established"

    return HistoryResult(
        score=score,
        vendor_prior_submissions=n,
        vendor_avg_amount=round(avg, 2),
        vendor_max_amount=round(mx, 2),
        vendor_min_amount=round(mn, 2),
        vendor_total_spend=round(total_spend, 2),
        vendor_stddev=round(std, 2),
        first_seen_date=first_seen,
        last_seen_date=last_seen,
        trust_status=trust_status,
        known_bank_accounts=known_accounts,
        known_ifsc_codes=known_ifscs,
        prior_invoices=prior_invoices,
        flags=flags,
    )