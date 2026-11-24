"""Licences: register, printing on invoices and renewal alerts."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.businesses.models import Branch
from app.modules.compliance.models import KIND_LABEL, PRINT_LABEL, Licence, LicenceKind
from app.modules.events import service as events

ALERT_STAGES = (90, 60, 30, 0)
FIELDS = ("branch_id", "kind", "name", "number", "issued_by", "issue_date", "expiry_date", "print_on_invoice",
          "file_id", "notes", "is_active")


def status_of(lic: Licence, today: date | None = None) -> dict[str, Any]:
    today = today or local_today()
    if not lic.is_active:
        return {"status": "inactive", "days_left": None}
    if lic.expiry_date is None:
        return {"status": "valid", "days_left": None}
    days = (lic.expiry_date - today).days
    return {"status": "expired" if days < 0 else "due" if days <= 60 else "valid", "days_left": days}


def _check(db: Session, access: BusinessAccess, lic: Licence) -> None:
    if lic.branch_id is not None:
        br = db.get(Branch, lic.branch_id)
        if br is None or br.business_id != access.business.id:
            raise ValidationFailed("Branch not found")
    if lic.file_id is not None:
        from app.modules.files.models import StoredFile

        f = db.get(StoredFile, lic.file_id)
        if f is None or f.business_id != access.business.id:
            raise ValidationFailed("Certificate file not found")
    if lic.issue_date and lic.expiry_date and lic.expiry_date < lic.issue_date:
        raise ValidationFailed("Expiry date is before the issue date")
    if not lic.name:
        lic.name = KIND_LABEL[lic.kind]


def create(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Licence:
    lic = Licence(business_id=access.business.id, **{k: v for k, v in data.items() if k in FIELDS})
    lic.name = (lic.name or "").strip()
    _check(db, access, lic)
    db.add(lic)
    db.flush()
    audit.record(db, action="licence.created", entity_type="licence", entity_id=lic.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"kind": lic.kind.value, "number": lic.number, "expiry_date": lic.expiry_date})
    return lic


def update(db: Session, access: BusinessAccess, lic: Licence, data: dict[str, Any]) -> Licence:
    before = {k: getattr(lic, k) for k in FIELDS}
    for k, v in data.items():
        if k in FIELDS:
            setattr(lic, k, v)
    if "expiry_date" in data and data["expiry_date"] != before["expiry_date"]:
        lic.alert_stage = None  # renewed: announce the new expiry again in time
    _check(db, access, lic)
    db.flush()
    audit.record(db, action="licence.updated", entity_type="licence", entity_id=lic.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={k: {"from": before[k], "to": getattr(lic, k)} for k in FIELDS if before[k] != getattr(lic, k)})
    return lic


def delete(db: Session, access: BusinessAccess, lic: Licence) -> None:
    audit.record(db, action="licence.deleted", entity_type="licence", entity_id=lic.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"kind": lic.kind.value, "number": lic.number})
    db.delete(lic)
    db.flush()


def get(db: Session, access: BusinessAccess, lid: uuid.UUID) -> Licence:
    lic = db.get(Licence, lid)
    if lic is None or lic.business_id != access.business.id:
        raise NotFound("Licence not found")
    return lic


def listing(db: Session, access: BusinessAccess) -> list[Licence]:
    return list(db.scalars(select(Licence).where(Licence.business_id == access.business.id)
                           .order_by(Licence.is_active.desc(), Licence.expiry_date.is_(None), Licence.expiry_date,
                                     Licence.name)))


def printable(db: Session, business_id: uuid.UUID, branch_id: uuid.UUID, on: date) -> list[dict[str, str]]:
    """Licence lines for an invoice header: active, marked to print, valid on the invoice date."""
    rows = db.scalars(select(Licence).where(
        Licence.business_id == business_id, Licence.is_active.is_(True), Licence.print_on_invoice.is_(True),
        Licence.kind != LicenceKind.GST,  # the GSTIN is printed already
        or_(Licence.branch_id.is_(None), Licence.branch_id == branch_id),
        or_(Licence.expiry_date.is_(None), Licence.expiry_date >= on)).order_by(Licence.kind, Licence.name))
    return [{"label": PRINT_LABEL.get(r.kind, r.name), "number": r.number} for r in rows]


def emit_renewal_alerts(db: Session, *, today: date | None = None) -> int:
    """Daily: licence.expiring at 90/60/30 days before expiry, licence.expired on the day; once each."""
    today = today or local_today()
    rows = db.scalars(select(Licence).where(Licence.is_active.is_(True), Licence.expiry_date.is_not(None),
                                            Licence.expiry_date <= date.fromordinal(today.toordinal() + 90)))
    n = 0
    for lic in rows:
        days = (lic.expiry_date - today).days
        stage = min(s for s in ALERT_STAGES if days <= s) if days <= 90 else None
        if stage is None or (lic.alert_stage is not None and stage >= lic.alert_stage):
            continue
        lic.alert_stage = stage
        events.emit(db, "licence.expired" if stage == 0 else "licence.expiring", aggregate_type="licence",
                    aggregate_id=lic.id, business_id=lic.business_id,
                    payload={"id": lic.id, "kind": lic.kind.value, "name": lic.name, "number": lic.number,
                             "expiry_date": lic.expiry_date, "days_left": days})
        n += 1
    db.commit()
    return n
