"""Expense claims and petty cash.

Staff submit a claim (date, expense head, amount, what for, photo of the bill). Someone with
``expenses.manage`` approves or rejects it; paying it records a normal expense paid from petty
cash, cash or bank — so it appears in the books, P&L and expense reports like any other.

Petty cash is its own cash account ("Petty Cash"); top it up from the till or the bank and see
its balance at any time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.claims.models import ClaimStatus, ExpenseClaim

ZERO = Decimal(0)


def submit(db: Session, access: BusinessAccess, data: dict[str, Any]) -> ExpenseClaim:
    from app.modules.accounting.accounts import get_account
    from app.modules.accounting.models import AccountType
    from app.modules.businesses.models import Branch

    access.require("expenses.claim")
    account = get_account(db, access, data["account_id"])
    if account.type != AccountType.EXPENSE:
        raise ValidationFailed("Choose an expense head")
    branch_id = data.get("branch_id") or db.scalar(select(Branch.id).where(
        Branch.business_id == access.business.id, Branch.kind == "head_office"))
    if not access.can_access_branch(branch_id):
        raise ValidationFailed("Branch not accessible")
    if data.get("file_id"):
        from app.modules.files.models import StoredFile

        f = db.get(StoredFile, data["file_id"])
        if f is None or f.business_id != access.business.id:
            raise ValidationFailed("Bill photo not found")
    c = ExpenseClaim(business_id=access.business.id, branch_id=branch_id, claimant_id=access.user.id,
                     expense_date=data["expense_date"], account_id=account.id, amount=data["amount"],
                     description=data["description"], file_id=data.get("file_id"), status=ClaimStatus.SUBMITTED)
    db.add(c)
    db.flush()
    audit.record(db, action="claim.submitted", entity_type="expense_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"amount": c.amount})
    from app.modules.events import service as events

    events.emit(db, "claim.submitted", aggregate_type="expense_claim", aggregate_id=c.id, business_id=access.business.id,
                payload={"id": c.id, "amount": c.amount, "description": c.description, "claimant_id": c.claimant_id})
    return c


def visible(db: Session, access: BusinessAccess) -> Select:
    stmt = select(ExpenseClaim).where(ExpenseClaim.business_id == access.business.id)
    if not access.can("expenses.manage"):
        stmt = stmt.where(ExpenseClaim.claimant_id == access.user.id)  # own claims only
    return stmt


def get(db: Session, access: BusinessAccess, cid: uuid.UUID) -> ExpenseClaim:
    c = db.scalar(visible(db, access).where(ExpenseClaim.id == cid))
    if c is None:
        raise NotFound("Claim not found")
    return c


def _decide(c: ExpenseClaim, access: BusinessAccess, status: ClaimStatus, note: str | None) -> None:
    c.status, c.decided_by_id, c.decided_at, c.decision_note = status, access.user.id, datetime.now(UTC), note


def approve(db: Session, access: BusinessAccess, c: ExpenseClaim, *, note: str | None,
            paid_from_account_id: uuid.UUID | None, on: date) -> ExpenseClaim:
    access.require("expenses.manage")
    if c.status != ClaimStatus.SUBMITTED:
        raise Conflict("This claim was already decided")
    if c.claimant_id == access.user.id and not access.is_owner:
        raise ValidationFailed("You cannot approve your own claim")
    _decide(c, access, ClaimStatus.APPROVED, note)
    audit.record(db, action="claim.approved", entity_type="expense_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"note": note})
    if paid_from_account_id:
        pay(db, access, c, paid_from_account_id=paid_from_account_id, on=on)
    db.flush()
    return c


def reject(db: Session, access: BusinessAccess, c: ExpenseClaim, *, note: str) -> ExpenseClaim:
    access.require("expenses.manage")
    if c.status != ClaimStatus.SUBMITTED:
        raise Conflict("This claim was already decided")
    _decide(c, access, ClaimStatus.REJECTED, note)
    audit.record(db, action="claim.rejected", entity_type="expense_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"note": note})
    db.flush()
    return c


def pay(db: Session, access: BusinessAccess, c: ExpenseClaim, *, paid_from_account_id: uuid.UUID, on: date) -> ExpenseClaim:
    from app.modules.accounting.expenses import create_expense
    from app.modules.users.models import User

    access.require("expenses.manage")
    if c.status != ClaimStatus.APPROVED:
        raise Conflict("Approve the claim before paying it")
    who = db.get(User, c.claimant_id)
    exp = create_expense(db, access, expense_date=c.expense_date, account_id=c.account_id, amount=c.amount,
                         description=f"{c.description} (claim by {who.full_name if who else 'staff'})"[:300],
                         paid_from_account_id=paid_from_account_id, branch_id=c.branch_id, reference=f"Claim {str(c.id)[:8]}")
    c.expense_id, c.status = exp.id, ClaimStatus.PAID
    if c.file_id:  # the bill photo now also belongs to the expense record
        from app.modules.files.models import StoredFile

        f = db.get(StoredFile, c.file_id)
        if f is not None and f.entity_type is None:
            f.entity_type, f.entity_id = "expense", exp.id
    db.flush()
    return c


# ---------------------------------------------------------------------------- petty cash


def petty_cash(db: Session, access: BusinessAccess) -> dict[str, Any]:
    from app.modules.accounting.chart import Accounts
    from app.modules.accounting.models import JournalLine

    access.require("expenses.manage")
    acc = Accounts(db, access.business.id)["petty_cash"]
    dr, cr = db.execute(select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
                        .where(JournalLine.account_id == acc.id)).one()
    pending = db.scalar(select(func.coalesce(func.sum(ExpenseClaim.amount), 0)).where(
        ExpenseClaim.business_id == access.business.id,
        ExpenseClaim.status.in_([ClaimStatus.SUBMITTED, ClaimStatus.APPROVED])))
    return {"account_id": acc.id, "balance": Decimal(dr) - Decimal(cr), "claims_waiting": Decimal(pending or 0)}


def top_up(db: Session, access: BusinessAccess, *, from_account_id: uuid.UUID, amount: Decimal, on: date,
           note: str | None) -> dict[str, Any]:
    from app.modules.accounting.accounts import get_account
    from app.modules.accounting.chart import Accounts
    from app.modules.accounting.ledger import Line, post
    from app.modules.accounting.models import AccountSubtype, JournalSource

    access.require("expenses.manage")
    if amount <= 0:
        raise ValidationFailed("Amount must be positive")
    src = get_account(db, access, from_account_id)
    pc = Accounts(db, access.business.id)["petty_cash"]
    if src.subtype not in (AccountSubtype.CASH, AccountSubtype.BANK) or src.id == pc.id:
        raise ValidationFailed("Top up from the cash till or a bank account")
    entry = post(db, access.business.id, entry_date=on, source_type=JournalSource.MANUAL,
                 lines=[Line(pc, debit=amount), Line(src, credit=amount)], created_by=access.user.id,
                 narration=f"Petty cash top-up from {src.name}" + (f": {note}" if note else ""))
    audit.record(db, action="petty_cash.topped_up", entity_type="journal_entry", entity_id=entry.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"amount": amount, "from": src.code})
    return petty_cash(db, access)
