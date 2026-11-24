"""Financial-year closing.

Closing posts one entry on the last day of the year that moves every income and expense
balance of the year into Retained Earnings, then locks the year: nothing more can be posted
into it (every posting path checks ``FinancialYear.is_closed``). Balance-sheet accounts simply
carry on. The owner can reopen a year, which reverses the closing entry.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.chart import Accounts
from app.modules.accounting.ledger import Line, post, reverse
from app.modules.accounting.models import (
    Account,
    AccountType,
    JournalEntry,
    JournalLine,
    JournalSource,
)
from app.modules.audit import service as audit
from app.modules.billing.models import DocStatus, Document
from app.modules.businesses.models import FinancialYear
from app.modules.businesses.service import financial_year_for
from app.modules.gst.tax import ZERO


def _fy(db: Session, access: BusinessAccess, fy_id: uuid.UUID) -> FinancialYear:
    fy = db.get(FinancialYear, fy_id)
    if fy is None or fy.business_id != access.business.id:
        raise NotFound("Financial year not found")
    return fy


def close_year(db: Session, access: BusinessAccess, fy_id: uuid.UUID) -> JournalEntry | None:
    access.require("financial_years.manage", "accounting.post")
    fy = _fy(db, access, fy_id)
    if fy.is_closed:
        raise Conflict(f"{fy.code} is already closed")
    earlier_open = db.scalar(select(FinancialYear.code).where(
        FinancialYear.business_id == fy.business_id, FinancialYear.end_date < fy.start_date,
        FinancialYear.is_closed.is_(False)))
    if earlier_open:
        raise ValidationFailed(f"Close {earlier_open} first")
    drafts = db.scalar(select(func.count()).where(
        Document.business_id == fy.business_id, Document.status == DocStatus.DRAFT,
        Document.doc_date >= fy.start_date, Document.doc_date <= fy.end_date))
    if drafts:
        raise ValidationFailed(f"{drafts} draft document(s) are dated in {fy.code}; finalize "
                               "or delete them first")

    rows = db.execute(
        select(Account, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .where(Account.business_id == fy.business_id,
               Account.type.in_([AccountType.INCOME, AccountType.EXPENSE]),
               JournalLine.entry_date >= fy.start_date, JournalLine.entry_date <= fy.end_date)
        .group_by(Account.id)).all()
    lines: list[Line] = []
    profit = ZERO
    for account, dr, cr in rows:
        balance = Decimal(dr or 0) - Decimal(cr or 0)  # debit-positive
        if balance:
            lines.append(Line(account, credit=balance) if balance > 0 else Line(account, debit=-balance))
            profit -= balance
    entry = None
    if lines:
        re = Accounts(db, fy.business_id)["retained_earnings"]
        lines.append(Line(re, credit=profit) if profit > 0 else Line(re, debit=-profit))
        # A year may be closed, reopened and closed again: each closing is its own event.
        n = db.scalar(select(func.count()).where(JournalEntry.source_type == JournalSource.CLOSING,
                                                 JournalEntry.source_id == fy.id))
        entry = post(db, fy.business_id, entry_date=fy.end_date, source_type=JournalSource.CLOSING,
                     source_id=fy.id, source_event=f"close{n}", source_ref=fy.code, lines=lines,
                     created_by=access.user.id,
                     narration=f"Closing {fy.code}: net profit {profit} to Retained Earnings")
    fy.is_closed = True
    fy.closed_at = datetime.now(UTC)
    # Make sure the next year exists so the business can keep trading.
    next_start = fy.end_date + timedelta(days=1)
    if not db.scalar(select(FinancialYear.id).where(FinancialYear.business_id == fy.business_id,
                                                    FinancialYear.start_date == next_start)):
        start, end, code = financial_year_for(next_start, access.business.fy_start_month)
        db.add(FinancialYear(business_id=fy.business_id, code=code, start_date=start, end_date=end))
    audit.record(db, action="financial_year.closed", entity_type="financial_year", entity_id=fy.id,
                 business_id=fy.business_id, actor_id=access.user.id,
                 changes={"code": fy.code, "net_profit": profit})
    return entry


def reopen_year(db: Session, access: BusinessAccess, fy_id: uuid.UUID, *, reason: str) -> FinancialYear:
    if not access.is_owner:
        raise PermissionDenied("Only the owner can reopen a closed financial year")
    fy = _fy(db, access, fy_id)
    if not fy.is_closed:
        raise Conflict(f"{fy.code} is not closed")
    later_closed = db.scalar(select(FinancialYear.code).where(
        FinancialYear.business_id == fy.business_id, FinancialYear.start_date > fy.end_date,
        FinancialYear.is_closed.is_(True)))
    if later_closed:
        raise ValidationFailed(f"Reopen {later_closed} first")
    fy.is_closed = False
    fy.closed_at = None
    db.flush()
    reversed_ids = select(JournalEntry.reverses_id).where(JournalEntry.reverses_id.is_not(None))
    closing = db.scalar(select(JournalEntry).where(
        JournalEntry.business_id == fy.business_id, JournalEntry.source_type == JournalSource.CLOSING,
        JournalEntry.source_id == fy.id, JournalEntry.reverses_id.is_(None),
        JournalEntry.id.not_in(reversed_ids)))
    if closing is not None:
        n = db.scalar(select(func.count()).where(JournalEntry.source_type == JournalSource.CLOSING,
                                                 JournalEntry.source_id == fy.id))
        reverse(db, closing, on=fy.end_date, created_by=access.user.id, source_event=f"reopen{n}",
                narration=f"Reopening {fy.code}: {reason}")
    audit.record(db, action="financial_year.reopened", entity_type="financial_year", entity_id=fy.id,
                 business_id=fy.business_id, actor_id=access.user.id, changes={"reason": reason})
    return fy
