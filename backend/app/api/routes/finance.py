"""Budgets, the Income-tax depreciation schedule, daily cash closing and balance confirmations."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import DB, require
from app.core.clock import local_today
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.finance import service
from app.modules.finance.models import BalanceConfirmation

router = APIRouter(prefix="/businesses/{business_id}", tags=["finance"])
public = APIRouter(prefix="/public/confirm", tags=["public balance confirmation"])
View = Annotated[BusinessAccess, Depends(require("accounting.view"))]
Post = Annotated[BusinessAccess, Depends(require("accounting.view", "accounting.post"))]
Settings = Annotated[BusinessAccess, Depends(require("business.settings"))]
Token = Annotated[str, Path(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
Amt = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)]


class BudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: uuid.UUID
    cost_centre_id: uuid.UUID | None = None
    fy_start: date
    months: list[Amt] = Field(min_length=12, max_length=12)


@router.put("/budgets")
def set_budget(body: BudgetIn, access: Post, db: DB) -> dict[str, Any]:
    rows = service.set_budget(db, access, account_id=body.account_id, cost_centre_id=body.cost_centre_id,
                              fy_start=body.fy_start, months=body.months)
    db.commit()
    return {"account_id": body.account_id, "months": [{"month": b.month, "amount": b.amount} for b in rows]}


@router.get("/budgets")
def budgets(access: View, db: DB, fy_start: date | None = None, cost_centre_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Budgets of a financial year by account, month by month."""
    from sqlalchemy import select

    from app.modules.finance.models import Budget

    fy = service._fy(db, access.business.id, fy_start or local_today())
    stmt = select(Budget).where(Budget.business_id == access.business.id, Budget.month >= fy.start_date, Budget.month <= fy.end_date)
    stmt = stmt.where(Budget.cost_centre_id.is_(None) if cost_centre_id is None else Budget.cost_centre_id == cost_centre_id)
    out: dict[str, dict[str, str]] = {}
    for b in db.scalars(stmt):
        out.setdefault(str(b.account_id), {})[b.month.isoformat()] = str(b.amount)
    return {"financial_year": fy.code, "from": fy.start_date, "to": fy.end_date,
            "months": [m.isoformat() for m in service._months(fy.start_date, fy.end_date)], "budgets": out}


@router.get("/budgets/report")
def budget_report(access: View, db: DB, date_from: date, date_to: date, cost_centre_id: uuid.UUID | None = None) -> dict[str, Any]:
    return service.budget_report(db, access, date_from=date_from, date_to=date_to, cost_centre_id=cost_centre_id)


class OpeningWdvIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fy_start: date
    blocks: dict[str, Amt]


@router.get("/assets/it-depreciation")
def it_depreciation(access: View, db: DB, fy_start: date | None = None) -> dict[str, Any]:
    return service.it_depreciation(db, access, fy_start=fy_start or local_today())


@router.put("/assets/it-opening-wdv")
def it_opening(body: OpeningWdvIn, access: Settings, db: DB) -> dict[str, Any]:
    from app.core.errors import ValidationFailed

    bad = set(body.blocks) - set(service.IT_BLOCKS)
    if bad:
        raise ValidationFailed(f"Unknown blocks: {', '.join(sorted(bad))}")
    b = access.business
    value = {"fy_start": body.fy_start.isoformat(), "blocks": {k: str(v) for k, v in body.blocks.items()}}
    b.settings = {**(b.settings or {}), "it_opening_wdv": value}
    audit.record(db, action="business.settings_updated", entity_type="business", entity_id=b.id, business_id=b.id,
                 actor_id=access.user.id, changes={"it_opening_wdv": value})
    db.commit()
    return value


class CloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    closing_date: date = Field(default_factory=local_today)
    account_id: uuid.UUID | None = None
    denominations: dict[str, Annotated[str, StringConstraints(max_length=12)] | int] = Field(default_factory=dict)
    notes: Annotated[str, StringConstraints(max_length=300)] | None = None


@router.get("/cash-closing/position")
def cash_position(access: View, db: DB, on: date | None = None, account_id: uuid.UUID | None = None) -> dict[str, Any]:
    return service.cash_position(db, access, on=on or local_today(), account_id=account_id)


@router.post("/cash-closing", status_code=status.HTTP_201_CREATED)
def close_cash(body: CloseIn, access: Post, db: DB) -> dict[str, Any]:
    c = service.close_cash(db, access, on=body.closing_date, account_id=body.account_id,
                           denominations=dict(body.denominations), notes=body.notes)
    db.commit()
    return service._closing_out(c)


@router.get("/cash-closing")
def list_closings(access: View, db: DB, date_from: date, date_to: date) -> list[dict[str, Any]]:
    return service.closings(db, access, date_from=date_from, date_to=date_to)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    party_id: uuid.UUID
    as_of: date = Field(default_factory=local_today)
    date_from: date | None = None


def _conf_out(c: BalanceConfirmation) -> dict[str, Any]:
    return {"id": c.id, "party_id": c.party_id, "party_name": c.statement.get("party"), "as_of": c.as_of, "balance": c.balance,
            "token": c.token, "status": c.status.value, "response_note": c.response_note, "their_balance": c.their_balance,
            "responded_at": c.responded_at, "created_at": c.created_at}


@router.post("/balance-confirmations", status_code=status.HTTP_201_CREATED)
def send_confirmation(body: ConfirmIn, access: View, db: DB) -> dict[str, Any]:
    c = service.send_confirmation(db, access, party_id=body.party_id, as_of=body.as_of, date_from=body.date_from)
    db.commit()
    return _conf_out(c)


@router.get("/balance-confirmations")
def list_confirmations(access: View, db: DB, party_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    return [_conf_out(c) for c in service.list_confirmations(db, access, party_id)]


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agree: bool
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    their_balance: Annotated[Decimal, Field(max_digits=15, decimal_places=2)] | None = None


@public.get("/{token}")
def view_confirmation(token: Token, db: DB) -> dict[str, Any]:
    """The statement a customer / supplier sees from the link (no login: the secret link is the key)."""
    c = service.by_token(db, token)
    return {"as_of": c.as_of, "balance": c.balance, "statement": c.statement, "status": c.status.value}


@public.post("/{token}")
def answer_confirmation(token: Token, body: AnswerIn, db: DB) -> dict[str, Any]:
    c = service.respond(db, token, agree=body.agree, note=body.note, their_balance=body.their_balance)
    db.commit()
    return {"status": c.status.value}
