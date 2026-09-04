"""Vendor-history service.

Computes statistical anomalies in a vendor's prior submissions: sudden
amount spikes, frequency anomalies, and "unfamiliar vendor" flags. Reads
from the SQLAlchemy document table.
"""

from __future__ import annotations

import logging
import math
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import DocumentRow
from app.schemas.models import (
    ExtractionResult,
    HistoryFlag,
    HistoryResult,
    Severity,
)

logger = logging.getLogger(__name__)


async def vendor_history(
    session: AsyncSession,
    extraction: ExtractionResult,
) -> HistoryResult:
    """Inspect vendor history and flag statistical anomalies."""
    vendor = (extraction.vendor or "").strip()
    amount = extraction.total_amount or 0.0
    if not vendor:
        return HistoryResult(score=0.0, flags=[])

    stmt = (
        select(DocumentRow)
        .where(DocumentRow.vendor == vendor)
        .order_by(DocumentRow.created_at.desc())
        .limit(200)
    )
    rows: Iterable[DocumentRow] = (await session.execute(stmt)).scalars().all()

    n = len(rows)
    if n == 0:
        return HistoryResult(
            score=0.6,
            vendor_prior_submissions=0,
            flags=[
                HistoryFlag(
                    code="unknown_vendor",
                    label="Vendor not seen before",
                    severity=Severity.MEDIUM,
                    detail=f"No prior submissions for '{vendor}' in our records.",
                    score=0.6,
                )
            ],
        )

    amounts = [float(r.total_amount or 0.0) for r in rows]
    avg = sum(amounts) / n
    var = sum((a - avg) ** 2 for a in amounts) / n
    std = math.sqrt(var)
    mx = max(amounts)

    flags: list[HistoryFlag] = []
    score = 0.0

    # Amount-spike: amount > avg + 2*std and amount > 1.5 * avg
    if std > 0 and amount > avg + 2 * std and amount > 1.5 * avg:
        flags.append(
            HistoryFlag(
                code="amount_spike",
                label="Amount spike",
                severity=Severity.HIGH,
                detail=(
                    f"${amount:,.2f} is {amount / max(avg, 1):.1f}× the vendor's "
                    f"average of ${avg:,.2f} (n={n}, std=${std:,.2f})."
                ),
                score=0.8,
            )
        )
        score = max(score, 0.8)

    # Single unusually-large submission
    if amount > 1.5 * mx and amount > 500:
        flags.append(
            HistoryFlag(
                code="largest_submission",
                label="Largest-ever submission",
                severity=Severity.MEDIUM,
                detail=(
                    f"Amount ${amount:,.2f} is {(amount / max(mx, 1)):.1f}× the "
                    f"vendor's previous max of ${mx:,.2f}."
                ),
                score=0.55,
            )
        )
        score = max(score, 0.55)

    # Frequency spike
    if n >= 3:
        last_3 = amounts[:3]
        if all(a > 0 for a in last_3) and amount > 1.4 * (sum(last_3) / 3):
            flags.append(
                HistoryFlag(
                    code="frequency_spike",
                    label="Recent frequency spike",
                    severity=Severity.MEDIUM,
                    detail=f"3 most-recent submissions for this vendor average "
                    f"${sum(last_3) / 3:,.2f}; this one is ${amount:,.2f}.",
                    score=0.5,
                )
            )
            score = max(score, 0.5)

    # Bank account change detection (Vendor Intelligence Agent)
    curr_bank_account = (extraction.bank_account or "").strip()
    curr_bank_routing = (extraction.bank_routing or "").strip()

    if curr_bank_account:
        prev_accounts = [
            r.bank_account.strip()
            for r in rows
            if r.bank_account and r.bank_account.strip()
        ]
        if prev_accounts and curr_bank_account not in prev_accounts:
            prev_acc = prev_accounts[0]
            masked_prev = f"...{prev_acc[-4:]}" if len(prev_acc) >= 4 else prev_acc
            masked_curr = f"...{curr_bank_account[-4:]}" if len(curr_bank_account) >= 4 else curr_bank_account
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
            r.bank_routing.strip()
            for r in rows
            if r.bank_routing and r.bank_routing.strip()
        ]
        if prev_routings and curr_bank_routing not in prev_routings:
            flags.append(
                HistoryFlag(
                    code="bank_routing_changed",
                    label="Bank routing number changed",
                    severity=Severity.HIGH,
                    detail=(
                        f"Vendor '{vendor}' bank routing/IFSC changed from {prev_routings[0]} "
                        f"to {curr_bank_routing}. Verify with vendor via out-of-band communication."
                    ),
                    score=0.80,
                )
            )
            score = max(score, 0.80)

    return HistoryResult(
        score=score,
        vendor_prior_submissions=n,
        vendor_avg_amount=round(avg, 2),
        vendor_max_amount=round(mx, 2),
        vendor_stddev=round(std, 2),
        flags=flags,
    )