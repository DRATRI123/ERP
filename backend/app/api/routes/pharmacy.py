"""Pharmacy: doctors, substitutes by salt, the schedule register, doctor-wise sales and the
near-expiry discount setting."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import DB, require
from app.api.routes.catalog import product_out
from app.api.schemas_masters import ProductOut
from app.core.clock import local_today
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.catalog import service as catalog
from app.modules.pharmacy import service
from app.modules.pharmacy.models import SCHEDULE_LABEL, Doctor, DrugSchedule

router = APIRouter(prefix="/businesses/{business_id}", tags=["pharmacy"])
ViewSales = Annotated[BusinessAccess, Depends(require("invoices.view"))]
ManageDoctors = Annotated[BusinessAccess, Depends(require("invoices.view", "customers.manage"))]
ViewProducts = Annotated[BusinessAccess, Depends(require("products.view"))]
Reports = Annotated[BusinessAccess, Depends(require("reports.view"))]
Settings = Annotated[BusinessAccess, Depends(require("business.settings"))]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]


class DoctorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    registration_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    qualification: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    clinic: Short | None = None
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=15)] | None = None
    is_active: bool = True


class DoctorUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] | None = None
    registration_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    qualification: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    clinic: Short | None = None
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=15)] | None = None
    is_active: bool | None = None


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    registration_no: str | None
    qualification: str | None
    clinic: str | None
    address: str | None
    phone: str | None
    is_active: bool


class NearExpiryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(ge=0, le=365, description="Batches expiring within this many days; 0 turns it off")
    percent: Decimal = Field(ge=0, le=100, decimal_places=2)


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    to = date_to or local_today()
    start = date_from or to.replace(day=1)
    if start > to:
        raise ValidationFailed("The start date is after the end date")
    return start, to


@router.get("/doctors", response_model=list[DoctorOut])
def list_doctors(access: ViewSales, db: DB, q: str | None = Query(None, max_length=100),
                 include_inactive: bool = False) -> list[Doctor]:
    return service.list_doctors(db, access, q=q, include_inactive=include_inactive)


@router.post("/doctors", response_model=DoctorOut, status_code=status.HTTP_201_CREATED)
def create_doctor(body: DoctorIn, access: ManageDoctors, db: DB) -> Doctor:
    d = service.create_doctor(db, access, body.model_dump())
    db.commit()
    return d


@router.patch("/doctors/{doctor_id}", response_model=DoctorOut)
def update_doctor(doctor_id: uuid.UUID, body: DoctorUpdateIn, access: ManageDoctors, db: DB) -> Doctor:
    d = service.update_doctor(db, access, service.get_doctor(db, access, doctor_id),
                              body.model_dump(exclude_unset=True))
    db.commit()
    return d


@router.get("/products/{product_id}/substitutes", response_model=list[ProductOut])
def substitutes(product_id: uuid.UUID, access: ViewProducts, db: DB) -> list[ProductOut]:
    """Products with the same salt / composition, cheapest first."""
    return product_out(db, access, service.substitutes(db, access, catalog.get_product(db, access, product_id)))


@router.get("/pharmacy/schedules")
def schedules(access: ViewProducts) -> list[dict[str, str]]:
    return [{"value": s.value, "label": label} for s, label in SCHEDULE_LABEL.items()]


@router.get("/reports/schedule-register")
def schedule_register(access: Reports, db: DB, date_from: date | None = None, date_to: date | None = None,
                      schedule: list[DrugSchedule] | None = Query(None)) -> dict[str, Any]:
    """Sales of Schedule H1, X and narcotic drugs (or the schedules asked for), line by line."""
    start, to = _period(date_from, date_to)
    rows = service.schedule_register(db, access, date_from=start, date_to=to, schedules=schedule)
    return {"date_from": start, "date_to": to, "rows": rows}


@router.get("/reports/doctor-sales")
def doctor_sales(access: Reports, db: DB, date_from: date | None = None,
                 date_to: date | None = None) -> dict[str, Any]:
    start, to = _period(date_from, date_to)
    return {"date_from": start, "date_to": to,
            "rows": service.doctor_sales(db, access, date_from=start, date_to=to)}


@router.get("/pharmacy/settings")
def get_settings(access: ViewSales) -> dict[str, Any]:
    rule = service.near_expiry_rule(access.business.settings or {})
    return {"near_expiry_discount": {"days": rule[0] if rule else 0, "percent": rule[1] if rule else Decimal(0)}}


@router.put("/pharmacy/settings/near-expiry")
def set_near_expiry(body: NearExpiryIn, access: Settings, db: DB) -> dict[str, Any]:
    business = access.business
    before = (business.settings or {}).get("near_expiry_discount")
    value = {"days": body.days, "percent": str(body.percent)}
    business.settings = {**(business.settings or {}), "near_expiry_discount": value}
    audit.record(db, action="business.settings_updated", entity_type="business", entity_id=business.id,
                 business_id=business.id, actor_id=access.user.id,
                 changes={"near_expiry_discount": {"before": before, "after": value}})
    db.commit()
    return get_settings(access)
