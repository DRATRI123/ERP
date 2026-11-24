"""Expense claims and petty cash."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from app.api.deps import DB, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.core.clock import local_today
from app.modules.access.service import BusinessAccess
from app.modules.claims import service
from app.modules.claims.models import ClaimStatus, ExpenseClaim

router = APIRouter(prefix="/businesses/{business_id}", tags=["claims"])
Claimant = Annotated[BusinessAccess, Depends(require("expenses.claim"))]
Approver = Annotated[BusinessAccess, Depends(require("expenses.manage"))]


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expense_date: date
    account_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    file_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    expense_date: date
    account_id: uuid.UUID
    account_name: str | None = None
    amount: Decimal
    description: str
    file_id: uuid.UUID | None
    status: ClaimStatus
    claimant_id: uuid.UUID
    claimant_name: str | None = None
    decided_at: datetime | None
    decision_note: str | None
    expense_id: uuid.UUID | None
    created_at: datetime


class DecisionIn(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    paid_from_account_id: uuid.UUID | None = None


class RejectIn(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class PayIn(BaseModel):
    paid_from_account_id: uuid.UUID
    paid_on: date | None = None


class TopUpIn(BaseModel):
    from_account_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    on: date | None = None
    note: Annotated[str, StringConstraints(max_length=200)] | None = None


def _out(db: DB, rows: list[ExpenseClaim]) -> list[ClaimOut]:
    from app.modules.accounting.models import Account
    from app.modules.users.models import User

    accounts = dict(db.execute(select(Account.id, Account.name).where(Account.id.in_({r.account_id for r in rows} or {uuid.uuid4()}))).all())
    users = dict(db.execute(select(User.id, User.full_name).where(User.id.in_({r.claimant_id for r in rows} or {uuid.uuid4()}))).all())
    return [ClaimOut.model_validate(r).model_copy(update={"account_name": accounts.get(r.account_id),
                                                           "claimant_name": users.get(r.claimant_id)}) for r in rows]


@router.get("/claims/heads")
def heads(access: Claimant, db: DB) -> list[dict[str, Any]]:
    """Expense heads a claim can be filed under (staff need not see the chart of accounts)."""
    from app.modules.accounting.models import Account, AccountType

    rows = db.scalars(select(Account).where(Account.business_id == access.business.id, Account.type == AccountType.EXPENSE,
                                            Account.is_active.is_(True), Account.system_key.is_(None)).order_by(Account.code))
    return [{"id": a.id, "code": a.code, "name": a.name} for a in rows]


@router.get("/claims/pay-from")
def pay_from(access: Approver, db: DB) -> list[dict[str, Any]]:
    """Cash, bank and petty cash accounts to pay claims from."""
    from app.modules.accounting.chart import Accounts
    from app.modules.accounting.models import Account, AccountSubtype

    Accounts(db, access.business.id)["petty_cash"]  # make sure it exists
    db.commit()
    rows = db.scalars(select(Account).where(Account.business_id == access.business.id, Account.is_active.is_(True),
                                            Account.subtype.in_([AccountSubtype.CASH, AccountSubtype.BANK])).order_by(Account.code))
    return [{"id": a.id, "code": a.code, "name": a.name, "petty": a.system_key == "petty_cash"} for a in rows]


@router.get("/claims", response_model=Page[ClaimOut])
def list_claims(access: Claimant, db: DB, params: Annotated[PageParams, Depends(page_params)],
                status_: ClaimStatus | None = None) -> Page[ClaimOut]:
    """Approvers see every claim; everyone else sees their own."""
    stmt = service.visible(db, access)
    if status_:
        stmt = stmt.where(ExpenseClaim.status == status_)
    rows, total = paginate(db, stmt.order_by(ExpenseClaim.created_at.desc()), params)
    return Page(items=_out(db, rows), total=total, limit=params.limit, offset=params.offset)


@router.post("/claims", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
def submit(body: ClaimIn, access: Claimant, db: DB) -> ClaimOut:
    c = service.submit(db, access, body.model_dump())
    db.commit()
    return _out(db, [c])[0]


@router.post("/claims/{claim_id}/approve", response_model=ClaimOut)
def approve(claim_id: uuid.UUID, body: DecisionIn, access: Approver, db: DB) -> ClaimOut:
    c = service.approve(db, access, service.get(db, access, claim_id), note=body.note,
                        paid_from_account_id=body.paid_from_account_id, on=local_today())
    db.commit()
    return _out(db, [c])[0]


@router.post("/claims/{claim_id}/reject", response_model=ClaimOut)
def reject(claim_id: uuid.UUID, body: RejectIn, access: Approver, db: DB) -> ClaimOut:
    c = service.reject(db, access, service.get(db, access, claim_id), note=body.note)
    db.commit()
    return _out(db, [c])[0]


@router.post("/claims/{claim_id}/pay", response_model=ClaimOut)
def pay(claim_id: uuid.UUID, body: PayIn, access: Approver, db: DB) -> ClaimOut:
    c = service.pay(db, access, service.get(db, access, claim_id), paid_from_account_id=body.paid_from_account_id,
                    on=body.paid_on or local_today())
    db.commit()
    return _out(db, [c])[0]


@router.get("/petty-cash")
def petty_cash(access: Approver, db: DB) -> dict[str, Any]:
    out = service.petty_cash(db, access)
    db.commit()  # the Petty Cash account may have just been created
    return out


@router.post("/petty-cash/top-up")
def top_up(body: TopUpIn, access: Approver, db: DB) -> dict[str, Any]:
    out = service.top_up(db, access, from_account_id=body.from_account_id, amount=body.amount, on=body.on or local_today(),
                         note=body.note)
    db.commit()
    return out
