"""Expense vouchers (with optional GST input credit)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.accounts import get_account, get_cost_centre
from app.modules.accounting.chart import Accounts
from app.modules.accounting.ledger import Line, open_year, post, reverse_source
from app.modules.accounting.models import (
    AccountSubtype,
    AccountType,
    Expense,
    ExpenseStatus,
    JournalSource,
)
from app.modules.audit import service as audit
from app.modules.businesses.models import Branch
from app.modules.events import service as events
from app.modules.gst.tax import LineInput, compute_line
from app.modules.numbering.service import next_number
from app.modules.parties.models import Party


def create_expense(db: Session, access: BusinessAccess, *, expense_date: date, account_id: uuid.UUID,
                   amount: Decimal, description: str, gst_rate: Decimal = Decimal(0),
                   itc_eligible: bool = True, is_interstate: bool | None = None,
                   paid_from_account_id: uuid.UUID | None = None, party_id: uuid.UUID | None = None,
                   cost_centre_id: uuid.UUID | None = None, reference: str | None = None,
                   branch_id: uuid.UUID | None = None) -> Expense:
    access.require("expenses.manage")
    account = get_account(db, access, account_id)
    if account.type != AccountType.EXPENSE:
        raise ValidationFailed("Choose an expense account")
    paid_from = get_account(db, access, paid_from_account_id) if paid_from_account_id else None
    if paid_from is not None and paid_from.subtype not in (AccountSubtype.CASH, AccountSubtype.BANK):
        raise ValidationFailed("Pay expenses from a cash or bank account")
    party = db.get(Party, party_id) if party_id else None
    if party_id and (party is None or party.business_id != access.business.id):
        raise ValidationFailed("Supplier not found")
    if paid_from is None and party is None:
        raise ValidationFailed("Give the cash/bank account it was paid from, or the supplier it is owed to")
    if cost_centre_id:
        get_cost_centre(db, access, cost_centre_id)
    branch_id = branch_id or db.scalar(select(Branch.id).where(
        Branch.business_id == access.business.id, Branch.kind == "head_office"))
    branch = db.get(Branch, branch_id)
    if branch is None or branch.business_id != access.business.id or not access.can_access_branch(branch.id):
        raise ValidationFailed("Branch not found or not accessible")
    if is_interstate is None:  # supplier in another state → IGST
        ours = branch.state_code or access.business.state_code
        is_interstate = bool(party and party.state_code and ours and party.state_code != ours)
    tax = compute_line(LineInput(quantity=Decimal(1), unit_price=amount, gst_rate=gst_rate),
                       interstate=is_interstate, charge_tax=gst_rate > 0)
    fy = open_year(db, access.business.id, expense_date)
    number = next_number(db, access.business.id, f"expense/{fy.code}", prefix=f"EXP/{fy.code}/")
    exp = Expense(business_id=access.business.id, branch_id=branch.id, number=number,
                  expense_date=expense_date, account_id=account.id,
                  paid_from_account_id=paid_from.id if paid_from else None, party_id=party_id,
                  cost_centre_id=cost_centre_id, description=description, reference=reference,
                  amount=tax.taxable_value, gst_rate=gst_rate, itc_eligible=itc_eligible,
                  is_interstate=is_interstate, cgst=tax.cgst, sgst=tax.sgst, igst=tax.igst,
                  total=tax.total, created_by_id=access.user.id)
    db.add(exp)
    db.flush()

    acc = Accounts(db, access.business.id)
    lines = [Line(account, debit=exp.amount + (Decimal(0) if itc_eligible else tax.tax),
                  cost_centre_id=cost_centre_id, description=description)]
    if itc_eligible:
        for head, amt in (("cgst", tax.cgst), ("sgst", tax.sgst), ("igst", tax.igst)):
            if amt:
                lines.append(Line(acc[f"input_{head}"], debit=amt))
    lines.append(Line(paid_from, credit=exp.total) if paid_from else
                 Line(acc["ap"], credit=exp.total, party_id=party_id))
    post(db, access.business.id, entry_date=expense_date, source_type=JournalSource.EXPENSE,
         source_id=exp.id, source_ref=number, lines=lines, branch_id=branch.id,
         created_by=access.user.id, narration=f"Expense {number}: {description}")
    audit.record(db, action="expense.created", entity_type="expense", entity_id=exp.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": number, "total": exp.total, "account": account.code})
    events.emit(db, "expense.created", aggregate_type="expense", aggregate_id=exp.id,
                business_id=access.business.id,
                payload={"id": exp.id, "number": number, "total": exp.total,
                         "account_id": account.id, "expense_date": expense_date})
    return exp


def get_expense(db: Session, access: BusinessAccess, eid: uuid.UUID) -> Expense:
    e = db.get(Expense, eid)
    if e is None or e.business_id != access.business.id or not access.can_access_branch(e.branch_id):
        raise NotFound("Expense not found")
    return e


def cancel_expense(db: Session, access: BusinessAccess, exp: Expense, *, reason: str, on: date) -> Expense:
    access.require("expenses.manage")
    if exp.status == ExpenseStatus.CANCELLED:
        raise Conflict("This expense is already cancelled")
    reverse_source(db, access.business.id, JournalSource.EXPENSE, exp.id, on=on, created_by=access.user.id)
    exp.status = ExpenseStatus.CANCELLED
    exp.cancelled_at = datetime.now(UTC)
    exp.cancel_reason = reason
    audit.record(db, action="expense.cancelled", entity_type="expense", entity_id=exp.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": exp.number, "reason": reason})
    return exp
