"""Leads and follow-ups, warranty / AMC and service calls, dispatch with proof of delivery, and
discount approvals."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from sqlalchemy import select

from app.api.deps import DB, require
from app.core.clock import local_today
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.billing import service as billing
from app.modules.crm import discounts, dispatch, leads, service_desk
from app.modules.crm.models import (
    ActivityKind,
    ContractKind,
    Dispatch,
    DispatchStatus,
    Lead,
    LeadStage,
    ServiceContract,
    ServiceTicket,
    TicketStatus,
)
from app.modules.parties.models import Party
from app.modules.users.models import User

router = APIRouter(prefix="/businesses/{business_id}", tags=["sales follow-up"])
CustView = Annotated[BusinessAccess, Depends(require("customers.view"))]
CustManage = Annotated[BusinessAccess, Depends(require("customers.view", "customers.manage"))]
InvView = Annotated[BusinessAccess, Depends(require("invoices.view"))]
InvCreate = Annotated[BusinessAccess, Depends(require("invoices.view", "invoices.create"))]
Shipper = Annotated[BusinessAccess, Depends(require("invoices.view", "dispatch.update"))]
Approver = Annotated[BusinessAccess, Depends(require("discounts.approve"))]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9+\- ]{6,20}$")]
Amount = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)]


def _names(db: DB, ids: set[uuid.UUID | None], model: Any = User, attr: str = "full_name") -> dict[uuid.UUID, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(db.execute(select(model.id, getattr(model, attr)).where(model.id.in_(ids))).all())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- leads


class LeadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    company: Text | None = None
    phone: Phone | None = None
    email: EmailStr | None = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    source: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    requirement: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    expected_value: Amount = Decimal(0)
    assigned_to_id: uuid.UUID | None = None
    next_follow_up: date | None = None


class LeadPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    company: Text | None = None
    phone: Phone | None = None
    email: EmailStr | None = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    source: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    requirement: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    expected_value: Amount | None = None
    assigned_to_id: uuid.UUID | None = None
    next_follow_up: date | None = None
    stage: LeadStage | None = None
    lost_reason: Text | None = None


class ActivityIn(BaseModel):
    kind: ActivityKind
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    next_follow_up: date | None = None


def _lead_out(db: DB, lead: Lead, users: dict[uuid.UUID, str] | None = None) -> dict[str, Any]:
    users = users if users is not None else _names(db, {lead.assigned_to_id})
    return {k: getattr(lead, k) for k in ("id", "name", "company", "phone", "email", "city", "source", "requirement",
                                          "stage", "expected_value", "assigned_to_id", "next_follow_up", "party_id",
                                          "quotation_id", "lost_reason", "closed_on", "created_at")} | {
        "assigned_to": users.get(lead.assigned_to_id) if lead.assigned_to_id else None,
        "overdue": bool(lead.next_follow_up and lead.next_follow_up < local_today())}


@router.get("/leads")
def list_leads(access: CustView, db: DB, stage: LeadStage | None = None, q: str | None = Query(None, max_length=100),
               due: str | None = Query(None, pattern="^(today|overdue)$"), mine: bool = False) -> list[dict[str, Any]]:
    rows = leads.listing(db, access, stage=stage, q=q, due=due, mine=mine)
    users = _names(db, {r.assigned_to_id for r in rows})
    return [_lead_out(db, r, users) for r in rows]


@router.get("/leads/pipeline")
def lead_pipeline(access: CustView, db: DB, date_from: date | None = None, date_to: date | None = None) -> dict[str, Any]:
    today = local_today()
    return leads.pipeline(db, access, date_from=date_from or today.replace(day=1), date_to=date_to or today)


@router.post("/leads", status_code=status.HTTP_201_CREATED)
def create_lead(body: LeadIn, access: CustManage, db: DB) -> dict[str, Any]:
    lead = leads.create(db, access, body.model_dump())
    db.commit()
    return _lead_out(db, lead)


@router.get("/leads/{lead_id}")
def get_lead(lead_id: uuid.UUID, access: CustView, db: DB) -> dict[str, Any]:
    lead = leads.get(db, access, lead_id)
    acts = leads.activities(db, lead)
    users = _names(db, {*(a.user_id for a in acts), lead.assigned_to_id})
    return _lead_out(db, lead, users) | {"activities": [
        {"id": a.id, "kind": a.kind, "note": a.note, "happened_at": a.happened_at, "by": users.get(a.user_id)} for a in acts]}


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: uuid.UUID, body: LeadPatch, access: CustManage, db: DB) -> dict[str, Any]:
    lead = leads.update(db, access, leads.get(db, access, lead_id), body.model_dump(exclude_unset=True))
    db.commit()
    return _lead_out(db, lead)


@router.post("/leads/{lead_id}/activities", status_code=status.HTTP_201_CREATED)
def log_activity(lead_id: uuid.UUID, body: ActivityIn, access: CustView, db: DB) -> dict[str, Any]:
    lead = leads.get(db, access, lead_id)
    leads.log(db, access, lead, kind=body.kind, note=body.note, next_follow_up=body.next_follow_up)
    db.commit()
    return _lead_out(db, lead)


@router.post("/leads/{lead_id}/convert")
def convert_lead(lead_id: uuid.UUID, access: CustManage, db: DB) -> dict[str, Any]:
    lead = leads.get(db, access, lead_id)
    party = leads.convert(db, access, lead)
    db.commit()
    return {"lead": _lead_out(db, lead), "party_id": party.id, "party_name": party.name}


# ---------------------------------------------------------------------------- warranty / AMC


class ContractIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ContractKind = ContractKind.AMC
    party_id: uuid.UUID | None = None
    customer_name: Text | None = None
    customer_phone: Phone | None = None
    product_id: uuid.UUID | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    serial_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    start_date: date = Field(default_factory=local_today)
    months: Annotated[int, Field(ge=1, le=120)] = 12
    end_date: date | None = None
    visits_included: Annotated[int, Field(ge=0, le=52)] = 0
    amount: Amount = Decimal(0)
    document_id: uuid.UUID | None = None
    notes: Annotated[str, StringConstraints(max_length=500)] | None = None


class RenewIn(BaseModel):
    months: Annotated[int, Field(ge=1, le=120)] = 12
    amount: Amount


def _contract_out(db: DB, c: ServiceContract, parties: dict[uuid.UUID, str] | None = None) -> dict[str, Any]:
    parties = parties if parties is not None else _names(db, {c.party_id}, Party, "name")
    return {k: getattr(c, k) for k in ("id", "kind", "number", "party_id", "customer_name", "customer_phone", "product_id",
                                       "description", "serial_no", "document_id", "start_date", "end_date",
                                       "visits_included", "amount", "renewed_from_id", "notes")} | {
        "party_name": parties.get(c.party_id) if c.party_id else c.customer_name, "state": service_desk.contract_state(c),
        "days_left": (c.end_date - local_today()).days}


@router.get("/service/contracts")
def list_contracts(access: InvView, db: DB, q: str | None = Query(None, max_length=100),
                   state: str | None = Query(None, pattern="^(active|expired)$"), kind: ContractKind | None = None,
                   ending_within: Annotated[int | None, Query(ge=0, le=365)] = None) -> list[dict[str, Any]]:
    rows = service_desk.contracts(db, access, q=q, state=state, kind=kind, ending_within=ending_within)
    parties = _names(db, {r.party_id for r in rows}, Party, "name")
    return [_contract_out(db, r, parties) for r in rows]


@router.post("/service/contracts", status_code=status.HTTP_201_CREATED)
def create_contract(body: ContractIn, access: InvCreate, db: DB) -> dict[str, Any]:
    c = service_desk.create_contract(db, access, body.model_dump())
    db.commit()
    return _contract_out(db, c)


@router.post("/service/contracts/{contract_id}/renew", status_code=status.HTTP_201_CREATED)
def renew_contract(contract_id: uuid.UUID, body: RenewIn, access: InvCreate, db: DB) -> dict[str, Any]:
    c = service_desk.renew(db, access, service_desk.get_contract(db, access, contract_id), months=body.months, amount=body.amount)
    db.commit()
    return _contract_out(db, c)


@router.post("/service/contracts/{contract_id}/cancel")
def cancel_contract(contract_id: uuid.UUID, access: InvCreate, db: DB) -> dict[str, Any]:
    c = service_desk.get_contract(db, access, contract_id)
    c.is_cancelled = True
    db.commit()
    return _contract_out(db, c)


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: uuid.UUID | None = None
    party_id: uuid.UUID | None = None
    customer_name: Text | None = None
    customer_phone: Phone | None = None
    product_id: uuid.UUID | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    serial_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    complaint: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
    assigned_to_id: uuid.UUID | None = None
    opened_on: date | None = None


class TicketPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: TicketStatus | None = None
    assigned_to_id: uuid.UUID | None = None
    resolution: LongText | None = None
    invoice_id: uuid.UUID | None = None


def _ticket_out(db: DB, t: ServiceTicket) -> dict[str, Any]:
    party = db.get(Party, t.party_id) if t.party_id else None
    who = db.get(User, t.assigned_to_id) if t.assigned_to_id else None
    c = db.get(ServiceContract, t.contract_id) if t.contract_id else None
    return {k: getattr(t, k) for k in ("id", "number", "opened_on", "contract_id", "party_id", "customer_name",
                                       "customer_phone", "product_id", "description", "serial_no", "complaint", "covered",
                                       "status", "assigned_to_id", "resolution", "resolved_on", "invoice_id")} | {
        "party_name": party.name if party else t.customer_name, "assigned_to": who.full_name if who else None,
        "contract": {"number": c.number, "kind": c.kind, "end_date": c.end_date} if c else None,
        "age_days": ((t.resolved_on or local_today()) - t.opened_on).days}


@router.get("/service/tickets")
def list_tickets(access: InvView, db: DB, status_: str | None = Query(None, alias="status"),
                 q: str | None = Query(None, max_length=100)) -> list[dict[str, Any]]:
    return [_ticket_out(db, t) for t in service_desk.tickets(db, access, status=status_, q=q)]


@router.post("/service/tickets", status_code=status.HTTP_201_CREATED)
def open_ticket(body: TicketIn, access: InvCreate, db: DB) -> dict[str, Any]:
    t = service_desk.open_ticket(db, access, body.model_dump())
    db.commit()
    return _ticket_out(db, t)


@router.patch("/service/tickets/{ticket_id}")
def update_ticket(ticket_id: uuid.UUID, body: TicketPatch, access: InvCreate, db: DB) -> dict[str, Any]:
    t = service_desk.update_ticket(db, access, service_desk.get_ticket(db, access, ticket_id), body.model_dump(exclude_unset=True))
    db.commit()
    return _ticket_out(db, t)


@router.get("/service/lookup")
def service_lookup(access: InvView, db: DB, q: Annotated[str, Query(min_length=2, max_length=100)]) -> dict[str, Any]:
    """Serial number, phone or name: the contracts and past service calls."""
    cs = service_desk.contracts(db, access, q=q, state=None, kind=None, ending_within=None)
    ts = service_desk.tickets(db, access, status=None, q=q)
    parties = _names(db, {c.party_id for c in cs}, Party, "name")
    return {"contracts": [_contract_out(db, c, parties) for c in cs[:50]], "tickets": [_ticket_out(db, t) for t in ts[:50]]}


# ---------------------------------------------------------------------------- dispatch


class DispatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: uuid.UUID
    packages: Annotated[int, Field(ge=1, le=10_000)] = 1
    vehicle_no: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, max_length=20)] | None = None
    driver_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    driver_phone: Phone | None = None
    transporter: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    lr_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    notes: LongText | None = None


class DispatchPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: DispatchStatus | None = None
    packages: Annotated[int, Field(ge=1, le=10_000)] | None = None
    vehicle_no: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, max_length=20)] | None = None
    driver_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    driver_phone: Phone | None = None
    transporter: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    lr_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    notes: LongText | None = None
    receiver_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    failure_reason: Text | None = None
    latitude: Annotated[Decimal, Field(ge=-90, le=90)] | None = None
    longitude: Annotated[Decimal, Field(ge=-180, le=180)] | None = None


def _dispatch_out(db: DB, d: Dispatch, doc: Any) -> dict[str, Any]:
    addr = (doc.party_snapshot or {}).get("shipping_address") or (doc.party_snapshot or {}).get("billing_address") or {}
    return {k: getattr(d, k) for k in ("id", "document_id", "status", "packages", "vehicle_no", "driver_name", "driver_phone",
                                       "transporter", "lr_no", "dispatched_at", "delivered_at", "receiver_name",
                                       "pod_file_id", "failure_reason", "notes", "latitude", "longitude", "created_at")} | {
        "number": doc.number, "doc_type": doc.doc_type, "doc_date": doc.doc_date, "party_name": doc.party_name,
        "amount": doc.amount_payable, "balance_due": doc.balance_due,
        "phone": (doc.party_snapshot or {}).get("phone"),
        "address": ", ".join(str(addr.get(k)) for k in ("address_line1", "address_line2", "city", "pincode") if addr.get(k)) or None}


@router.get("/dispatches")
def list_dispatches(access: InvView, db: DB, status_: str | None = Query(None, alias="status"),
                    delivered_on: date | None = None) -> list[dict[str, Any]]:
    return [_dispatch_out(db, d, doc) for d, doc in dispatch.listing(db, access, status=status_, on=delivered_on)]


@router.get("/dispatches/to-send")
def to_send(access: InvView, db: DB, days: Annotated[int, Query(ge=1, le=90)] = 30) -> list[dict[str, Any]]:
    return [{"id": d.id, "number": d.number, "doc_type": d.doc_type, "doc_date": d.doc_date, "party_name": d.party_name,
             "amount": d.amount_payable} for d in dispatch.not_dispatched(db, access, days=days)]


@router.post("/dispatches", status_code=status.HTTP_201_CREATED)
def create_dispatch(body: DispatchIn, access: Shipper, db: DB) -> dict[str, Any]:
    d = dispatch.create(db, access, body.document_id, body.model_dump(exclude={"document_id"}))
    db.commit()
    return _dispatch_out(db, d, billing.get_document(db, access, d.document_id))


@router.patch("/dispatches/{dispatch_id}")
def update_dispatch(dispatch_id: uuid.UUID, body: DispatchPatch, access: Shipper, db: DB) -> dict[str, Any]:
    d = dispatch.update(db, access, dispatch.get(db, access, dispatch_id), body.model_dump(exclude_unset=True))
    db.commit()
    return _dispatch_out(db, d, billing.get_document(db, access, d.document_id))


@router.post("/dispatches/{dispatch_id}/pod")
async def upload_pod(dispatch_id: uuid.UUID, access: Shipper, db: DB, file: UploadFile = File(...),
                     receiver_name: Annotated[str | None, Form(max_length=100)] = None,
                     deliver: Annotated[bool, Form()] = True,
                     latitude: Annotated[Decimal | None, Form(ge=-90, le=90)] = None,
                     longitude: Annotated[Decimal | None, Form(ge=-180, le=180)] = None) -> dict[str, Any]:
    """Photo of the signed copy. With ``deliver`` (default) the dispatch is marked delivered."""
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise ValidationFailed("Photos up to 10 MB")
    d = dispatch.get(db, access, dispatch_id)
    dispatch.attach_pod(db, access, d, data=data, filename=file.filename or "pod.jpg")
    if deliver and d.status != DispatchStatus.DELIVERED:
        if d.status in (DispatchStatus.PENDING, DispatchStatus.PACKED):
            dispatch.update(db, access, d, {"status": DispatchStatus.OUT})
        dispatch.update(db, access, d, {"status": DispatchStatus.DELIVERED, "receiver_name": receiver_name,
                                        "latitude": latitude, "longitude": longitude})
    db.commit()
    return _dispatch_out(db, d, billing.get_document(db, access, d.document_id))


# ---------------------------------------------------------------------------- discount approval


class ApprovalRequestIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


class DecisionIn(BaseModel):
    approve: bool
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


@router.post("/documents/{document_id}/discount-approval", status_code=status.HTTP_201_CREATED)
def request_discount_approval(document_id: uuid.UUID, body: ApprovalRequestIn, access: InvCreate, db: DB) -> dict[str, Any]:
    doc = billing.get_document(db, access, document_id)
    billing.recompute(db, access, doc)
    a = discounts.request(db, access, doc, reason=body.reason)
    db.commit()
    return {"id": a.id, "discount_percent": a.discount_percent, "limit_percent": a.limit_percent, "expires_at": a.code_expires_at}


@router.get("/discount-approvals")
def list_approvals(access: InvView, db: DB, pending: bool = True) -> list[dict[str, Any]]:
    return discounts.listing(db, access, pending_only=pending)


@router.post("/discount-approvals/{approval_id}/decide")
def decide_approval(approval_id: uuid.UUID, body: DecisionIn, access: Approver, db: DB) -> dict[str, Any]:
    a = discounts.decide(db, access, discounts.get(db, access, approval_id), approve=body.approve, note=body.note)
    db.commit()
    return {"id": a.id, "status": a.status, "decided_at": a.decided_at}


