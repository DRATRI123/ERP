"""Pharmacy rules: prescriptions for scheduled drugs, the schedule register, doctors,
substitutes by salt and the automatic near-expiry discount."""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document, DocumentLine
from app.modules.businesses.modules import is_enabled
from app.modules.catalog.models import Product
from app.modules.pharmacy.models import (
    PRESCRIPTION_REQUIRED,
    REGISTER_SCHEDULES,
    SCHEDULE_LABEL,
    Doctor,
    DrugSchedule,
)

ZERO = Decimal(0)
NEAR_EXPIRY_FLAG = "near_expiry"
DOCTOR_FIELDS = ["name", "registration_no", "qualification", "clinic", "address", "phone", "is_active"]


def normalise_composition(text: str | None) -> str | None:
    """"Paracetamol  500mg " -> "Paracetamol 500mg": one space, trimmed, so equal salts match."""
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


# ---------------------------------------------------------------------------- doctors


def list_doctors(db: Session, access: BusinessAccess, *, q: str | None = None,
                 include_inactive: bool = False) -> list[Doctor]:
    stmt = select(Doctor).where(Doctor.business_id == access.business.id)
    if not include_inactive:
        stmt = stmt.where(Doctor.is_active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Doctor.name).like(like), func.lower(Doctor.clinic).like(like),
                              Doctor.registration_no == q, Doctor.phone == q))
    return list(db.scalars(stmt.order_by(Doctor.name)))


def get_doctor(db: Session, access: BusinessAccess, doctor_id: uuid.UUID) -> Doctor:
    d = db.get(Doctor, doctor_id)
    if d is None or d.business_id != access.business.id:
        raise NotFound("Doctor not found")
    return d


def _check_registration(db: Session, access: BusinessAccess, reg: str | None, current: Doctor | None) -> None:
    if not reg:
        return
    stmt = select(Doctor.id).where(Doctor.business_id == access.business.id, Doctor.registration_no == reg)
    if current is not None:
        stmt = stmt.where(Doctor.id != current.id)
    if db.scalar(stmt):
        raise Conflict(f"Another doctor already has registration number {reg}")


def create_doctor(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Doctor:
    access.require("customers.manage")
    _check_registration(db, access, data.get("registration_no"), None)
    d = Doctor(business_id=access.business.id, **data)
    db.add(d)
    db.flush()
    audit.record(db, action="doctor.created", entity_type="doctor", entity_id=d.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes=audit.snapshot(d, DOCTOR_FIELDS))
    return d


def update_doctor(db: Session, access: BusinessAccess, d: Doctor, data: dict[str, Any]) -> Doctor:
    access.require("customers.manage")
    if "registration_no" in data:
        _check_registration(db, access, data["registration_no"], d)
    before = audit.snapshot(d, DOCTOR_FIELDS)
    for k, v in data.items():
        setattr(d, k, v)
    db.flush()
    audit.record(db, action="doctor.updated", entity_type="doctor", entity_id=d.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes=audit.diff(before, audit.snapshot(d, DOCTOR_FIELDS)))
    return d


# ---------------------------------------------------------------------------- substitutes


def substitutes(db: Session, access: BusinessAccess, product: Product) -> list[Product]:
    """Other active products with the same salt / composition (case-insensitive)."""
    comp = normalise_composition(product.composition)
    if not comp:
        return []
    return list(db.scalars(select(Product).where(
        Product.business_id == access.business.id, Product.is_active.is_(True), Product.id != product.id,
        func.lower(Product.composition) == comp.lower()).order_by(Product.selling_price, Product.name)))


# ---------------------------------------------------------------------------- prescriptions


def check_prescription(db: Session, doc: Document, settings: dict[str, Any] | None) -> None:
    """A sale of a Schedule H/H1/X/narcotic drug must name the doctor and the patient; H1, X and
    narcotics (which go in the schedule register) also need the prescription number."""
    if not is_enabled(settings, "pharmacy"):
        return
    scheduled: dict[DrugSchedule, list[str]] = {}
    for ln in doc.lines:
        if ln.product_id is None:
            continue
        p = db.get(Product, ln.product_id)
        if p is not None and p.drug_schedule in PRESCRIPTION_REQUIRED:
            scheduled.setdefault(p.drug_schedule, []).append(p.name)
    if not scheduled:
        return
    names = ", ".join(sorted({n for v in scheduled.values() for n in v}))
    missing = []
    if doc.doctor_id is None:
        missing.append("the prescribing doctor")
    if not doc.patient_name:
        missing.append("the patient's name")
    if not doc.rx_number and any(s in REGISTER_SCHEDULES for s in scheduled):
        missing.append("the prescription (Rx) number")
    if missing:
        raise ValidationFailed(f"{names} need a prescription: enter {' and '.join(missing)}",
                               code="prescription_required",
                               details={"schedules": sorted(s.value for s in scheduled)})


def schedule_register(db: Session, access: BusinessAccess, *, date_from: date, date_to: date,
                      schedules: list[DrugSchedule] | None = None) -> list[dict[str, Any]]:
    """Sales of scheduled drugs: the H1 / X / narcotic register inspectors ask for."""
    wanted = schedules or list(REGISTER_SCHEDULES)
    stmt = (
        select(Document, DocumentLine, Product, Doctor)
        .join(DocumentLine, DocumentLine.document_id == Document.id)
        .join(Product, Product.id == DocumentLine.product_id)
        .outerjoin(Doctor, Doctor.id == Document.doctor_id)
        .where(Document.business_id == access.business.id, Document.doc_type == DocType.SALES_INVOICE,
               Document.status == DocStatus.FINAL, Document.doc_date >= date_from,
               Document.doc_date <= date_to, Product.drug_schedule.in_(wanted))
        .order_by(Document.doc_date, Document.number, DocumentLine.line_no)
    )
    if not access.all_branches:
        stmt = stmt.where(Document.branch_id.in_(access.branch_ids))
    out = []
    for doc, ln, p, doctor in db.execute(stmt).all():
        out.append({
            "date": doc.doc_date, "number": doc.number, "document_id": doc.id,
            "patient_name": doc.patient_name, "patient_address": doc.patient_address,
            "rx_number": doc.rx_number, "doctor_name": doctor.name if doctor else None,
            "doctor_registration": doctor.registration_no if doctor else None,
            "doctor_address": (doctor.address or doctor.clinic) if doctor else None,
            "product_name": p.name, "composition": p.composition, "schedule": p.drug_schedule,
            "schedule_label": SCHEDULE_LABEL[p.drug_schedule], "batch_no": ln.batch_no,
            "expiry_date": ln.expiry_date, "quantity": ln.quantity, "unit": ln.unit_code,
        })
    return out


def doctor_sales(db: Session, access: BusinessAccess, *, date_from: date, date_to: date) -> list[dict[str, Any]]:
    """Sales value and bill count per prescribing doctor (bills without a doctor are left out)."""
    stmt = (
        select(Doctor.id, Doctor.name, Doctor.clinic, func.count(Document.id),
               func.coalesce(func.sum(Document.taxable_total), 0), func.coalesce(func.sum(Document.grand_total), 0))
        .join(Document, Document.doctor_id == Doctor.id)
        .where(Document.business_id == access.business.id, Document.doc_type == DocType.SALES_INVOICE,
               Document.status == DocStatus.FINAL, Document.doc_date >= date_from, Document.doc_date <= date_to)
        .group_by(Doctor.id, Doctor.name, Doctor.clinic)
    )
    if not access.all_branches:
        stmt = stmt.where(Document.branch_id.in_(access.branch_ids))
    rows = [{"doctor_id": i, "doctor_name": n, "clinic": c, "bills": int(b), "taxable": Decimal(t),
             "total": Decimal(g)} for i, n, c, b, t, g in db.execute(stmt).all()]
    return sorted(rows, key=lambda r: r["total"], reverse=True)


# ---------------------------------------------------------------------------- near-expiry discount


def near_expiry_rule(settings: dict[str, Any]) -> tuple[int, Decimal] | None:
    """``settings["near_expiry_discount"] = {"days": 60, "percent": 10}``; off when missing or zero."""
    rule = settings.get("near_expiry_discount") or {}
    try:
        days, pct = int(rule.get("days") or 0), Decimal(str(rule.get("percent") or 0))
    except (TypeError, ValueError, ArithmeticError):
        return None
    if days <= 0 or pct <= 0 or pct > 100:
        return None
    return days, pct


def apply_near_expiry_discount(db: Session, access: BusinessAccess, doc: Document) -> None:
    """Give the configured discount on lines whose batch (chosen, or the one first-expiry-first-out
    will pick) expires within the configured days. Lines with their own discount are left alone."""
    rule = near_expiry_rule(access.business.settings or {})
    if rule is None or not is_enabled(access.business.settings, "pharmacy"):
        return
    days, pct = rule
    from app.modules.billing.service import _default_warehouse
    from app.modules.inventory.expiry import available_batches
    from app.modules.inventory.models import Batch
    from app.modules.pricing.service import FREE_FLAG

    warehouse_id = doc.warehouse_id
    for ln in doc.lines:
        flags = ln.custom_fields or {}
        if ln.product_id is None or flags.get(FREE_FLAG) or ln.discount_percent or ln.discount_amount:
            continue
        p = db.get(Product, ln.product_id)
        if p is None or not p.batch_tracking:
            continue
        expiry: date | None = None
        if ln.batch_no:
            b = db.scalar(select(Batch).where(Batch.product_id == p.id, Batch.batch_no == ln.batch_no))
            expiry = b.expiry_date if b else ln.expiry_date
        else:
            if warehouse_id is None:
                try:
                    warehouse_id = _default_warehouse(db, access, doc.branch_id)
                except ValidationFailed:  # no warehouse yet: nothing to look up
                    return
            batches = available_batches(db, access.business.id, p.id, warehouse_id, on=doc.doc_date)
            # Only when the first batch covers the whole line, so the whole line gets the discount.
            if batches and batches[0][1] >= ln.quantity:
                expiry = batches[0][0].expiry_date
        if expiry is not None and 0 <= (expiry - doc.doc_date).days <= days:
            ln.discount_percent = pct
            ln.custom_fields = {**flags, NEAR_EXPIRY_FLAG: True}
