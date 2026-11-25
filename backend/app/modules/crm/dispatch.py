"""Getting goods to the customer: pending → packed → out with a vehicle → delivered (with the
receiver's name and a photo of the signed copy) or failed (with why)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document
from app.modules.crm.models import Dispatch, DispatchStatus

SHIPPABLE = (DocType.SALES_INVOICE, DocType.DELIVERY_CHALLAN)
NEXT: dict[DispatchStatus, tuple[DispatchStatus, ...]] = {
    DispatchStatus.PENDING: (DispatchStatus.PACKED, DispatchStatus.OUT),
    DispatchStatus.PACKED: (DispatchStatus.OUT, DispatchStatus.PENDING),
    DispatchStatus.OUT: (DispatchStatus.DELIVERED, DispatchStatus.FAILED),
    DispatchStatus.FAILED: (DispatchStatus.OUT, DispatchStatus.PENDING),
    DispatchStatus.DELIVERED: (),
}
DETAILS = ("packages", "vehicle_no", "driver_name", "driver_phone", "transporter", "lr_no", "notes")


def create(db: Session, access: BusinessAccess, document_id: uuid.UUID, data: dict[str, Any]) -> Dispatch:
    access.require("dispatch.update")
    doc = db.get(Document, document_id)
    if doc is None or doc.business_id != access.business.id:
        raise NotFound("Document not found")
    if doc.doc_type not in SHIPPABLE or doc.status != DocStatus.FINAL:
        raise ValidationFailed("Only a final invoice or delivery challan can be dispatched")
    if db.scalar(select(Dispatch.id).where(Dispatch.document_id == doc.id)):
        raise Conflict(f"{doc.number} is already in dispatch")
    d = Dispatch(business_id=access.business.id, document_id=doc.id, status=DispatchStatus.PENDING,
                 updated_by_id=access.user.id, **{k: data[k] for k in DETAILS if data.get(k) is not None})
    d.vehicle_no = d.vehicle_no or doc.vehicle_no  # from the e-way bill details
    d.transporter = d.transporter or doc.transporter_name
    db.add(d)
    db.flush()
    return d


def get(db: Session, access: BusinessAccess, dispatch_id: uuid.UUID) -> Dispatch:
    d = db.get(Dispatch, dispatch_id)
    if d is None or d.business_id != access.business.id:
        raise NotFound("Dispatch not found")
    return d


def update(db: Session, access: BusinessAccess, d: Dispatch, data: dict[str, Any]) -> Dispatch:
    access.require("dispatch.update")
    for k in DETAILS:
        if k in data and data[k] is not None:
            setattr(d, k, data[k])
    st = data.get("status")
    if st:
        _move(db, access, d, DispatchStatus(st), data)
    d.updated_by_id = access.user.id
    return d


def _move(db: Session, access: BusinessAccess, d: Dispatch, st: DispatchStatus, data: dict[str, Any]) -> None:
    if st == d.status:
        return
    if st not in NEXT[d.status]:
        raise Conflict(f"Cannot go from {d.status.value} to {st.value}")
    now = datetime.now(UTC)
    if st == DispatchStatus.OUT:
        d.dispatched_at, d.failure_reason = now, None
    if st == DispatchStatus.DELIVERED:
        receiver = (data.get("receiver_name") or d.receiver_name or "").strip()
        if not receiver and not (data.get("pod_file_id") or d.pod_file_id):
            raise ValidationFailed("Enter who received it, or add the photo of the signed copy")
        d.receiver_name = receiver or None
        d.delivered_at = now
        if data.get("latitude") is not None and data.get("longitude") is not None:
            d.latitude, d.longitude = Decimal(str(data["latitude"])), Decimal(str(data["longitude"]))
    if st == DispatchStatus.FAILED:
        reason = (data.get("failure_reason") or "").strip()
        if not reason:
            raise ValidationFailed("Say why it was not delivered")
        d.failure_reason = reason[:200]
    old = d.status
    d.status = st
    audit.record(db, action="dispatch.status", entity_type="document", entity_id=d.document_id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"from": old.value, "to": st.value})


def attach_pod(db: Session, access: BusinessAccess, d: Dispatch, *, data: bytes, filename: str) -> Dispatch:
    """Photo of the signed delivery copy; stored in the document library against the bill."""
    access.require("dispatch.update")
    from app.modules.files import service as files

    doc = db.get(Document, d.document_id)
    f = files.upload(db, access, data=data, filename=filename, folder="Proof of delivery",
                     description=f"Proof of delivery — {doc.number if doc else ''}", tags=["pod"],
                     entity_type="document", entity_id=d.document_id)
    d.pod_file_id = f.id
    return d


def listing(db: Session, access: BusinessAccess, *, status: str | None, on: date | None) -> list[tuple[Dispatch, Document]]:
    access.require("invoices.view")
    stmt = select(Dispatch, Document).join(Document, Document.id == Dispatch.document_id).where(
        Dispatch.business_id == access.business.id)
    if status == "open":
        stmt = stmt.where(Dispatch.status.in_((DispatchStatus.PENDING, DispatchStatus.PACKED, DispatchStatus.OUT, DispatchStatus.FAILED)))
    elif status:
        stmt = stmt.where(Dispatch.status == DispatchStatus(status))
    if on:
        start = datetime.combine(on, datetime.min.time(), UTC) - timedelta(hours=6)
        stmt = stmt.where(Dispatch.delivered_at >= start, Dispatch.delivered_at < start + timedelta(days=1, hours=12))
    return list(db.execute(stmt.order_by(Dispatch.created_at.desc()).limit(500)).all())


def not_dispatched(db: Session, access: BusinessAccess, *, days: int = 30) -> list[Document]:
    """Recent final invoices and challans to a customer that are not in dispatch yet."""
    access.require("invoices.view")
    have = select(Dispatch.document_id).where(Dispatch.business_id == access.business.id)
    return list(db.scalars(select(Document).where(
        Document.business_id == access.business.id, Document.doc_type.in_(SHIPPABLE), Document.status == DocStatus.FINAL,
        Document.party_id.is_not(None), Document.doc_date >= local_today() - timedelta(days=days), Document.id.not_in(have))
        .order_by(Document.doc_date.desc(), Document.number.desc()).limit(200)))
