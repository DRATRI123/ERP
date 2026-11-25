"""Warranty, AMC and service calls.

A product with ``warranty_months`` gets a warranty registered when it is sold on a sales invoice
(one per serial number, or one per line), cancelled if the invoice is cancelled. AMCs are added
by hand (and renewed). A service call looks up the serial number or the customer, and is marked
"covered" when a warranty or AMC is running that day.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.models import Document
from app.modules.catalog.models import Product
from app.modules.crm.models import ContractKind, ServiceContract, ServiceTicket, TicketStatus
from app.modules.numbering.service import next_number
from app.modules.parties.models import Party

OPEN_STATES = (TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.WAITING_PARTS)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


def _fy_code(db: Session, business_id: uuid.UUID, on: date) -> str:
    from app.modules.inventory.service import _open_fy

    try:
        return _open_fy(db, business_id, on).code
    except Exception:  # noqa: BLE001 - a closed year still numbers by calendar year
        return str(on.year)


# ---------------------------------------------------------------------------- warranties from sales


def register_warranties(db: Session, business_id: uuid.UUID, doc: Document) -> int:
    """Called when a sales invoice is finalized."""
    n = 0
    for ln in doc.lines:
        if ln.product_id is None or ln.quantity <= 0:
            continue
        p = db.get(Product, ln.product_id)
        months = getattr(p, "warranty_months", None) if p else None
        if not months:
            continue
        end = add_months(doc.doc_date, int(months)) - timedelta(days=1)
        serials: list[str | None] = list(ln.serials or []) or [None]
        for serial in serials:
            number = next_number(db, business_id, f"warranty/{doc.doc_date.year}", prefix=f"WAR/{doc.doc_date.year}/", padding=5)
            db.add(ServiceContract(business_id=business_id, kind=ContractKind.WARRANTY, number=number, party_id=doc.party_id,
                                   customer_name=doc.party_name, customer_phone=(doc.party_snapshot or {}).get("phone"),
                                   product_id=ln.product_id, description=ln.description[:300], serial_no=serial,
                                   document_id=doc.id, line_id=ln.id, start_date=doc.doc_date, end_date=end,
                                   notes=None if serial else f"Quantity {ln.quantity}"))
            n += 1
    return n


def cancel_for_document(db: Session, doc: Document) -> None:
    for c in db.scalars(select(ServiceContract).where(ServiceContract.document_id == doc.id,
                                                      ServiceContract.kind == ContractKind.WARRANTY)):
        c.is_cancelled = True


# ---------------------------------------------------------------------------- contracts


def contract_state(c: ServiceContract, on: date | None = None) -> str:
    on = on or local_today()
    if c.is_cancelled:
        return "cancelled"
    if on < c.start_date:
        return "upcoming"
    return "active" if on <= c.end_date else "expired"


def create_contract(db: Session, access: BusinessAccess, data: dict[str, Any]) -> ServiceContract:
    access.require("invoices.create")
    kind = ContractKind(data["kind"])
    party_id = data.get("party_id")
    if party_id:
        party = db.get(Party, party_id)
        if party is None or party.business_id != access.business.id:
            raise ValidationFailed("Customer not found")
    elif not data.get("customer_name"):
        raise ValidationFailed("Choose the customer or enter their name")
    product_id = data.get("product_id")
    description = (data.get("description") or "").strip()
    if product_id:
        p = db.get(Product, product_id)
        if p is None or p.business_id != access.business.id:
            raise ValidationFailed("Product not found")
        description = description or p.name
    if not description:
        raise ValidationFailed("Describe the machine or item covered")
    start: date = data["start_date"]
    end: date = data.get("end_date") or (add_months(start, int(data.get("months") or 12)) - timedelta(days=1))
    if end < start:
        raise ValidationFailed("The contract ends before it starts")
    prefix = "AMC" if kind == ContractKind.AMC else "WAR"
    c = ServiceContract(business_id=access.business.id, kind=kind,
                        number=next_number(db, access.business.id, f"{prefix.lower()}/{start.year}", prefix=f"{prefix}/{start.year}/", padding=5),
                        party_id=party_id, customer_name=data.get("customer_name"), customer_phone=data.get("customer_phone"),
                        product_id=product_id, description=description[:300], serial_no=(data.get("serial_no") or "").strip() or None,
                        document_id=data.get("document_id"), start_date=start, end_date=end,
                        visits_included=int(data.get("visits_included") or 0), amount=Decimal(data.get("amount") or 0),
                        renewed_from_id=data.get("renewed_from_id"), notes=data.get("notes"))
    db.add(c)
    db.flush()
    audit.record(db, action="service_contract.created", entity_type="service_contract", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"number": c.number, "kind": kind.value})
    return c


def get_contract(db: Session, access: BusinessAccess, cid: uuid.UUID) -> ServiceContract:
    c = db.get(ServiceContract, cid)
    if c is None or c.business_id != access.business.id:
        raise NotFound("Contract not found")
    return c


def renew(db: Session, access: BusinessAccess, c: ServiceContract, *, months: int, amount: Decimal) -> ServiceContract:
    """A new AMC from the day after this one ends (or today, if it has lapsed)."""
    if c.is_cancelled:
        raise Conflict("This contract was cancelled")
    if db.scalar(select(ServiceContract.id).where(ServiceContract.renewed_from_id == c.id, ServiceContract.is_cancelled.is_(False))):
        raise Conflict("Already renewed")
    start = max(c.end_date + timedelta(days=1), local_today())
    return create_contract(db, access, {"kind": ContractKind.AMC, "party_id": c.party_id, "customer_name": c.customer_name,
                                        "customer_phone": c.customer_phone, "product_id": c.product_id,
                                        "description": c.description, "serial_no": c.serial_no, "start_date": start,
                                        "months": months, "visits_included": c.visits_included, "amount": amount,
                                        "renewed_from_id": c.id})


def contracts(db: Session, access: BusinessAccess, *, q: str | None, state: str | None, kind: ContractKind | None,
              ending_within: int | None) -> list[ServiceContract]:
    access.require("invoices.view")
    stmt = select(ServiceContract).where(ServiceContract.business_id == access.business.id)
    today = local_today()
    if kind:
        stmt = stmt.where(ServiceContract.kind == kind)
    if q:
        like = f"%{q.strip()}%"
        party_ids = select(Party.id).where(Party.business_id == access.business.id,
                                           or_(Party.name.ilike(like), Party.phone.like(like)))
        stmt = stmt.where(or_(ServiceContract.serial_no.ilike(like), ServiceContract.customer_name.ilike(like),
                              ServiceContract.customer_phone.like(like), ServiceContract.number.ilike(like),
                              ServiceContract.description.ilike(like), ServiceContract.party_id.in_(party_ids)))
    if state == "active":
        stmt = stmt.where(ServiceContract.is_cancelled.is_(False), ServiceContract.start_date <= today, ServiceContract.end_date >= today)
    elif state == "expired":
        stmt = stmt.where(ServiceContract.is_cancelled.is_(False), ServiceContract.end_date < today)
    if ending_within is not None:
        renewed = select(ServiceContract.renewed_from_id).where(ServiceContract.renewed_from_id.is_not(None),
                                                                 ServiceContract.is_cancelled.is_(False))
        stmt = stmt.where(ServiceContract.is_cancelled.is_(False), ServiceContract.end_date >= today - timedelta(days=30),
                          ServiceContract.end_date <= today + timedelta(days=ending_within), ServiceContract.id.not_in(renewed))
    return list(db.scalars(stmt.order_by(ServiceContract.end_date).limit(500)))


def covering(db: Session, business_id: uuid.UUID, *, serial_no: str | None, party_id: uuid.UUID | None,
             product_id: uuid.UUID | None, on: date) -> ServiceContract | None:
    stmt = select(ServiceContract).where(ServiceContract.business_id == business_id, ServiceContract.is_cancelled.is_(False),
                                         ServiceContract.start_date <= on, ServiceContract.end_date >= on)
    if serial_no:
        stmt = stmt.where(ServiceContract.serial_no == serial_no)
    elif party_id and product_id:
        stmt = stmt.where(ServiceContract.party_id == party_id, ServiceContract.product_id == product_id)
    else:
        return None
    return db.scalar(stmt.order_by(ServiceContract.end_date.desc()).limit(1))


# ---------------------------------------------------------------------------- service calls


def open_ticket(db: Session, access: BusinessAccess, data: dict[str, Any]) -> ServiceTicket:
    access.require("invoices.create")
    on: date = data.get("opened_on") or local_today()
    contract = None
    if data.get("contract_id"):
        contract = get_contract(db, access, data["contract_id"])
    serial = (data.get("serial_no") or (contract.serial_no if contract else None) or "").strip() or None
    if contract is None and serial:  # the warranty / AMC on this serial number, running or not
        contract = db.scalar(select(ServiceContract).where(
            ServiceContract.business_id == access.business.id, ServiceContract.serial_no == serial,
            ServiceContract.is_cancelled.is_(False)).order_by(ServiceContract.end_date.desc()).limit(1))
    party_id = data.get("party_id") or (contract.party_id if contract else None)
    product_id = data.get("product_id") or (contract.product_id if contract else None)
    if party_id:
        party = db.get(Party, party_id)
        if party is None or party.business_id != access.business.id:
            raise ValidationFailed("Customer not found")
    if not party_id and not data.get("customer_name") and not contract:
        raise ValidationFailed("Choose the customer or enter their name")
    running = covering(db, access.business.id, serial_no=serial, party_id=party_id, product_id=product_id, on=on)
    contract = running or contract
    covered = bool(contract and contract_state(contract, on) == "active")
    t = ServiceTicket(business_id=access.business.id,
                      number=next_number(db, access.business.id, f"service/{_fy_code(db, access.business.id, on)}",
                                         prefix=f"SRV/{_fy_code(db, access.business.id, on)}/"),
                      opened_on=on, contract_id=contract.id if contract else None, party_id=party_id,
                      customer_name=data.get("customer_name") or (contract.customer_name if contract else None),
                      customer_phone=data.get("customer_phone") or (contract.customer_phone if contract else None),
                      product_id=product_id, description=data.get("description") or (contract.description if contract else None),
                      serial_no=serial, complaint=data["complaint"].strip()[:1000], covered=covered,
                      assigned_to_id=data.get("assigned_to_id"), created_by_id=access.user.id)
    db.add(t)
    db.flush()
    audit.record(db, action="service_ticket.opened", entity_type="service_ticket", entity_id=t.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"number": t.number, "covered": covered})
    return t


def get_ticket(db: Session, access: BusinessAccess, tid: uuid.UUID) -> ServiceTicket:
    t = db.get(ServiceTicket, tid)
    if t is None or t.business_id != access.business.id:
        raise NotFound("Service call not found")
    return t


def update_ticket(db: Session, access: BusinessAccess, t: ServiceTicket, data: dict[str, Any]) -> ServiceTicket:
    access.require("invoices.create")
    if t.status == TicketStatus.CLOSED and data.get("status") not in (None, TicketStatus.CLOSED.value):
        pass  # reopening is allowed
    if "assigned_to_id" in data:
        t.assigned_to_id = data["assigned_to_id"]
    if data.get("resolution") is not None:
        t.resolution = data["resolution"].strip()[:1000] or None
    if data.get("invoice_id"):
        d = db.get(Document, data["invoice_id"])
        if d is None or d.business_id != access.business.id:
            raise ValidationFailed("Invoice not found")
        t.invoice_id = d.id
    if data.get("status"):
        st = TicketStatus(data["status"])
        if st in (TicketStatus.RESOLVED, TicketStatus.CLOSED) and not t.resolution:
            raise ValidationFailed("Write what was done before resolving")
        t.status = st
        t.resolved_on = local_today() if st in (TicketStatus.RESOLVED, TicketStatus.CLOSED) else None
    return t


def tickets(db: Session, access: BusinessAccess, *, status: str | None, q: str | None) -> list[ServiceTicket]:
    access.require("invoices.view")
    stmt = select(ServiceTicket).where(ServiceTicket.business_id == access.business.id)
    if status == "open":
        stmt = stmt.where(ServiceTicket.status.in_(OPEN_STATES))
    elif status:
        stmt = stmt.where(ServiceTicket.status == TicketStatus(status))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ServiceTicket.number.ilike(like), ServiceTicket.serial_no.ilike(like),
                              ServiceTicket.customer_name.ilike(like), ServiceTicket.customer_phone.like(like)))
    return list(db.scalars(stmt.order_by(ServiceTicket.opened_on.desc(), ServiceTicket.number.desc()).limit(500)))
