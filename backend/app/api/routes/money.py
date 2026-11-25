"""Cheques (post-dated register, clearing, bounces, printing), TDS and interest on overdue bills."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import DB, require
from app.api.schemas_billing import DocumentOut
from app.core.clock import local_today
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.models import PaymentDirection
from app.modules.money import service
from app.modules.money.models import Cheque, ChequeStatus, TdsDeposit
from app.modules.parties.models import Party

router = APIRouter(prefix="/businesses/{business_id}", tags=["money"])
View = Annotated[BusinessAccess, Depends(require("payments.view"))]
Create = Annotated[BusinessAccess, Depends(require("payments.view", "payments.create"))]
Settings = Annotated[BusinessAccess, Depends(require("business.settings"))]
Interest = Annotated[BusinessAccess, Depends(require("invoices.view"))]
InterestCreate = Annotated[BusinessAccess, Depends(require("invoices.view", "invoices.create"))]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]


class AllocationIn(BaseModel):
    document_id: uuid.UUID
    amount: Money


class ChequeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: PaymentDirection = Field(description="in = received from a customer, out = issued to a supplier")
    party_id: uuid.UUID
    cheque_no: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9A-Za-z-]{1,20}$")]
    cheque_date: date
    bank_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    amount: Money
    account_id: uuid.UUID | None = Field(None, description="Our bank account it goes into / is drawn on")
    branch_id: uuid.UUID | None = None
    payee_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    allocations: list[AllocationIn] = Field(default_factory=list, max_length=50)
    notes: Annotated[str, StringConstraints(max_length=300)] | None = None


class ChequeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    direction: PaymentDirection
    party_id: uuid.UUID
    party_name: str | None = None
    cheque_no: str
    cheque_date: date
    bank_name: str | None
    amount: Decimal
    account_id: uuid.UUID | None
    payee_name: str | None
    allocations: list[dict[str, Any]]
    notes: str | None
    status: ChequeStatus
    payment_id: uuid.UUID | None
    cleared_on: date | None
    bounced_on: date | None
    bounce_reason: str | None
    days_to_date: int = Field(0, description="Days until the cheque date (negative: past)")


class ClearIn(BaseModel):
    on: date = Field(default_factory=local_today)


class BounceIn(BaseModel):
    on: date = Field(default_factory=local_today)
    reason: Reason


class CancelIn(BaseModel):
    reason: Reason


class LayoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layout: dict[str, float | bool] = Field(description="Positions in mm; keys as returned by GET")


class DepositIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: date = Field(description="Any day of the month the TDS was deducted in")
    section: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10)] | None = None
    amount: Money
    deposit_date: date = Field(default_factory=local_today)
    challan_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    bsr_code: Annotated[str, StringConstraints(pattern=r"^[0-9]{7}$")] | None = None
    account_id: uuid.UUID


class DepositOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    month: date
    section: str | None
    amount: Decimal
    deposit_date: date
    challan_no: str | None
    bsr_code: str | None


class InterestSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate: Decimal = Field(ge=0, le=60, decimal_places=2, description="% per year; 0 turns it off")
    grace_days: int = Field(0, ge=0, le=365)


class InterestNotesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    party_id: uuid.UUID
    as_of: date = Field(default_factory=local_today)
    document_ids: list[uuid.UUID] | None = None


def _out(db: DB, c: Cheque, today: date) -> ChequeOut:
    party = db.get(Party, c.party_id)
    data = {k: getattr(c, k) for k in ChequeOut.model_fields if hasattr(c, k)}
    return ChequeOut.model_validate({**data, "party_name": party.name if party else None,
                                     "days_to_date": (c.cheque_date - today).days})


# ---------------------------------------------------------------------------- cheques


@router.get("/cheques", response_model=list[ChequeOut])
def list_cheques(access: View, db: DB, status_: ChequeStatus | None = Query(None, alias="status"),
                 direction: PaymentDirection | None = None, due_within_days: int | None = Query(None, ge=0, le=365)) -> list[ChequeOut]:
    today = local_today()
    due_by = date.fromordinal(today.toordinal() + due_within_days) if due_within_days is not None else None
    return [_out(db, c, today) for c in service.list_cheques(db, access, status=status_, direction=direction, due_by=due_by)]


@router.get("/cheques/summary")
def cheque_summary(access: View, db: DB) -> dict[str, Any]:
    return service.cheque_summary(db, access)


@router.post("/cheques", response_model=ChequeOut, status_code=status.HTTP_201_CREATED)
def create_cheque(body: ChequeIn, access: Create, db: DB) -> ChequeOut:
    c = service.create_cheque(db, access, body.model_dump())
    db.commit()
    return _out(db, c, local_today())


@router.post("/cheques/{cheque_id}/clear", response_model=ChequeOut)
def clear_cheque(cheque_id: uuid.UUID, body: ClearIn, access: Create, db: DB) -> ChequeOut:
    c = service.clear_cheque(db, access, service.get_cheque(db, access, cheque_id), on=body.on)
    db.commit()
    return _out(db, c, local_today())


@router.post("/cheques/{cheque_id}/bounce", response_model=ChequeOut)
def bounce_cheque(cheque_id: uuid.UUID, body: BounceIn, access: Create, db: DB) -> ChequeOut:
    c = service.bounce_cheque(db, access, service.get_cheque(db, access, cheque_id), on=body.on, reason=body.reason)
    db.commit()
    return _out(db, c, local_today())


@router.post("/cheques/{cheque_id}/cancel", response_model=ChequeOut)
def cancel_cheque(cheque_id: uuid.UUID, body: CancelIn, access: Create, db: DB) -> ChequeOut:
    c = service.cancel_cheque(db, access, service.get_cheque(db, access, cheque_id), reason=body.reason)
    db.commit()
    return _out(db, c, local_today())


@router.get("/cheques/{cheque_id}/print", response_class=HTMLResponse)
def print_cheque(cheque_id: uuid.UUID, access: View, db: DB, test: bool = False) -> HTMLResponse:
    """The cheque, laid out for printing on the bank's leaf (CTS-2010 size). ``test`` outlines the leaf."""
    return HTMLResponse(service.print_cheque(db, access, service.get_cheque(db, access, cheque_id), test=test))


@router.get("/cheque-layout")
def get_layout(access: View) -> dict[str, Any]:
    return {"layout": service.cheque_layout(access.business.settings), "defaults": service.DEFAULT_LAYOUT}


@router.put("/cheque-layout")
def set_layout(body: LayoutIn, access: Settings, db: DB) -> dict[str, Any]:
    unknown = set(body.layout) - set(service.DEFAULT_LAYOUT)
    if unknown:
        raise ValidationFailed(f"Unknown layout keys: {', '.join(sorted(unknown))}")
    business = access.business
    business.settings = {**(business.settings or {}), "cheque_layout": {**service.cheque_layout(business.settings), **body.layout}}
    audit.record(db, action="business.settings_updated", entity_type="business", entity_id=business.id,
                 business_id=business.id, actor_id=access.user.id, changes={"cheque_layout": body.layout})
    db.commit()
    return get_layout(access)


@router.get("/cheque-layout/sample", response_class=HTMLResponse)
def sample_cheque(access: View, payee: str = Query("Sample Payee Pvt Ltd", max_length=120),
                  amount: Decimal = Query(Decimal("125430.50"), gt=0, le=99_999_999_999)) -> HTMLResponse:
    """Print on plain paper and hold it against a cheque leaf to check the positions."""
    return HTMLResponse(service.render_cheque(payee=payee, amount=amount, on=local_today(),
                                              layout=service.cheque_layout(access.business.settings), test=True))


# ---------------------------------------------------------------------------- TDS


@router.get("/tds/sections")
def tds_sections(access: View) -> list[dict[str, Any]]:
    return service.TDS_SECTIONS


@router.get("/reports/tds")
def tds_register(access: View, db: DB, direction: PaymentDirection = PaymentDirection.OUT,
                 date_from: date | None = None, date_to: date | None = None) -> dict[str, Any]:
    """``out``: TDS we deducted on payments (with deposit position); ``in``: TDS customers deducted (claim it in the return)."""
    to = date_to or local_today()
    start = date_from or to.replace(day=1)
    if start > to:
        raise ValidationFailed("The start date is after the end date")
    return service.tds_register(db, access, direction=direction, date_from=start, date_to=to)


@router.get("/tds/deposits", response_model=list[DepositOut])
def list_deposits(access: View, db: DB) -> list[TdsDeposit]:
    return service.list_deposits(db, access)


@router.post("/tds/deposits", response_model=DepositOut, status_code=status.HTTP_201_CREATED)
def record_deposit(body: DepositIn, access: Create, db: DB) -> TdsDeposit:
    dep = service.record_deposit(db, access, body.model_dump())
    db.commit()
    return dep


# ---------------------------------------------------------------------------- interest


@router.get("/interest")
def interest(access: Interest, db: DB, as_of: date | None = None, party_id: uuid.UUID | None = None) -> dict[str, Any]:
    day = as_of or local_today()
    return {"as_of": day, "settings": service.interest_settings(access.business.settings),
            "parties": service.overdue_interest(db, access, as_of=day, party_id=party_id)}


@router.put("/interest/settings")
def interest_settings(body: InterestSettingsIn, access: Settings, db: DB) -> dict[str, Any]:
    business = access.business
    value = {"rate": str(body.rate), "grace_days": body.grace_days}
    business.settings = {**(business.settings or {}), "interest": value}
    audit.record(db, action="business.settings_updated", entity_type="business", entity_id=business.id,
                 business_id=business.id, actor_id=access.user.id, changes={"interest": value})
    db.commit()
    return service.interest_settings(business.settings)


@router.post("/interest/debit-notes", response_model=list[DocumentOut], status_code=status.HTTP_201_CREATED)
def interest_notes(body: InterestNotesIn, access: InterestCreate, db: DB) -> list[Any]:
    """Draft debit notes (one per overdue invoice) charging the interest; review and finalize them."""
    docs = service.draft_interest_notes(db, access, as_of=body.as_of, party_id=body.party_id,
                                        document_ids=body.document_ids)
    db.commit()
    return [DocumentOut.model_validate(d) for d in docs]
