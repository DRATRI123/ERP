"""Posting journal entries. The only place journal rows are created.

``post`` refuses anything that does not balance, any line with both or neither side, posting
into a closed (or missing) financial year, and inactive or foreign accounts. ``reverse``
creates the mirror entry; the original stays untouched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, ValidationFailed
from app.modules.accounting.models import Account, JournalEntry, JournalLine, JournalSource
from app.modules.businesses.models import FinancialYear
from app.modules.events import service as events
from app.modules.gst.tax import ZERO
from app.modules.numbering.service import next_number

Q4 = Decimal("0.0001")


@dataclass
class Line:
    account: Account
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    party_id: uuid.UUID | None = None
    cost_centre_id: uuid.UUID | None = None
    description: str | None = None


def open_year(db: Session, business_id: uuid.UUID, on: date) -> FinancialYear:
    fy = db.scalar(select(FinancialYear).where(
        FinancialYear.business_id == business_id, FinancialYear.start_date <= on,
        FinancialYear.end_date >= on))
    if fy is None:
        raise ValidationFailed(f"No financial year covers {on.isoformat()}; create it first")
    if fy.is_closed:
        raise ValidationFailed(f"Financial year {fy.code} is closed; books for it are locked")
    return fy


def net(lines: list[Line]) -> list[Line]:
    """Merge lines on the same account/party/cost centre and drop zero lines, so an entry
    shows e.g. one Sales credit rather than one per invoice line."""
    merged: dict[tuple, Decimal] = {}
    meta: dict[tuple, Line] = {}
    for ln in lines:
        key = (ln.account.id, ln.party_id, ln.cost_centre_id, ln.description)
        merged[key] = merged.get(key, ZERO) + ln.debit - ln.credit
        meta.setdefault(key, ln)
    out = []
    for key, amount in merged.items():
        amount = amount.quantize(Q4)
        if amount == 0:
            continue
        m = meta[key]
        out.append(Line(m.account, debit=amount if amount > 0 else ZERO,
                        credit=-amount if amount < 0 else ZERO, party_id=m.party_id,
                        cost_centre_id=m.cost_centre_id, description=m.description))
    return out


def post(db: Session, business_id: uuid.UUID, *, entry_date: date, source_type: JournalSource,
         lines: list[Line], source_id: uuid.UUID | None = None, source_event: str = "post",
         source_ref: str | None = None, narration: str | None = None,
         branch_id: uuid.UUID | None = None, created_by: uuid.UUID | None = None,
         reverses: JournalEntry | None = None, emit: bool = True) -> JournalEntry | None:
    lines = net(lines)
    if not lines:
        return None  # nothing to record (e.g. a zero-value document)
    for ln in lines:
        if ln.account.business_id != business_id:
            raise ValidationFailed("Account belongs to another business")
        if not ln.account.is_active and reverses is None:
            raise ValidationFailed(f"Account {ln.account.code} {ln.account.name} is inactive")
        if ln.debit < 0 or ln.credit < 0 or (ln.debit == 0) == (ln.credit == 0):
            raise ValidationFailed("Each journal line needs either a debit or a credit")
    dr = sum((ln.debit for ln in lines), ZERO)
    cr = sum((ln.credit for ln in lines), ZERO)
    if dr != cr:
        raise ValidationFailed(f"Journal does not balance: debit {dr} ≠ credit {cr}",
                               code="unbalanced_journal")
    if source_id is not None and db.scalar(select(JournalEntry.id).where(
            JournalEntry.business_id == business_id, JournalEntry.source_type == source_type,
            JournalEntry.source_id == source_id, JournalEntry.source_event == source_event)):
        raise Conflict("This event is already posted to the books")
    fy = open_year(db, business_id, entry_date)
    number = next_number(db, business_id, f"journal/{fy.code}", prefix=f"JV/{fy.code}/", padding=5)
    entry = JournalEntry(
        business_id=business_id, branch_id=branch_id, financial_year_id=fy.id, number=number,
        entry_date=entry_date, source_type=source_type, source_id=source_id,
        source_event=source_event, source_ref=source_ref, narration=narration,
        reverses_id=reverses.id if reverses else None, created_by_id=created_by)
    for i, ln in enumerate(lines, 1):
        entry.lines.append(JournalLine(
            line_no=i, business_id=business_id, account_id=ln.account.id, entry_date=entry_date,
            debit=ln.debit, credit=ln.credit, party_id=ln.party_id,
            cost_centre_id=ln.cost_centre_id, description=ln.description))
    db.add(entry)
    db.flush()
    if emit:
        events.emit(db, "journal.created", aggregate_type="journal_entry", aggregate_id=entry.id,
                    business_id=business_id,
                    payload={"id": entry.id, "number": number, "entry_date": entry_date,
                             "source_type": source_type.value, "source_ref": source_ref,
                             "total": dr})
    return entry


def reverse(db: Session, entry: JournalEntry, *, on: date, created_by: uuid.UUID | None,
            source_event: str = "reverse", narration: str | None = None) -> JournalEntry:
    if db.scalar(select(JournalEntry.id).where(JournalEntry.reverses_id == entry.id)):
        raise Conflict(f"Journal {entry.number} is already reversed")
    accounts = {a.id: a for a in db.scalars(select(Account).where(
        Account.id.in_({ln.account_id for ln in entry.lines})))}
    lines = [Line(accounts[ln.account_id], debit=ln.credit, credit=ln.debit, party_id=ln.party_id,
                  cost_centre_id=ln.cost_centre_id, description=ln.description)
             for ln in entry.lines]
    out = post(db, entry.business_id, entry_date=on, source_type=entry.source_type,
               source_id=entry.source_id, source_event=source_event, source_ref=entry.source_ref,
               narration=narration or f"Reversal of {entry.number}", branch_id=entry.branch_id,
               created_by=created_by, lines=lines, reverses=entry)
    assert out is not None
    return out


def reverse_source(db: Session, business_id: uuid.UUID, source_type: JournalSource,
                   source_id: uuid.UUID, *, on: date, created_by: uuid.UUID | None) -> JournalEntry | None:
    """Reverse the posting of a business event (cancelled invoice, payment, ...), if any."""
    entry = db.scalar(select(JournalEntry).where(
        JournalEntry.business_id == business_id, JournalEntry.source_type == source_type,
        JournalEntry.source_id == source_id, JournalEntry.source_event == "post"))
    if entry is None:
        return None
    return reverse(db, entry, on=on, created_by=created_by, source_event="cancel")
