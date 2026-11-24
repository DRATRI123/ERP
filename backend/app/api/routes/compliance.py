"""Licence and compliance register."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import DB, require
from app.modules.access.service import BusinessAccess
from app.modules.compliance import service
from app.modules.compliance.models import KIND_LABEL, Licence, LicenceKind

router = APIRouter(prefix="/businesses/{business_id}", tags=["compliance"])
View = Annotated[BusinessAccess, Depends(require("business.view"))]
Manage = Annotated[BusinessAccess, Depends(require("business.view", "licences.manage"))]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]


class LicenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: LicenceKind
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = ""
    number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    branch_id: uuid.UUID | None = None
    issued_by: Text | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    print_on_invoice: bool = False
    file_id: uuid.UUID | None = None
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    is_active: bool = True


class LicenceUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: LicenceKind | None = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] | None = None
    branch_id: uuid.UUID | None = None
    issued_by: Text | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    print_on_invoice: bool | None = None
    file_id: uuid.UUID | None = None
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    is_active: bool | None = None


class LicenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: LicenceKind
    kind_label: str
    name: str
    number: str
    branch_id: uuid.UUID | None
    issued_by: str | None
    issue_date: date | None
    expiry_date: date | None
    print_on_invoice: bool
    file_id: uuid.UUID | None
    notes: str | None
    is_active: bool
    status: str = Field(description="valid, due (≤ 60 days), expired or inactive")
    days_left: int | None
    updated_at: datetime


def _out(lic: Licence) -> LicenceOut:
    return LicenceOut.model_validate({**{c: getattr(lic, c) for c in (
        "id", "kind", "name", "number", "branch_id", "issued_by", "issue_date", "expiry_date", "print_on_invoice",
        "file_id", "notes", "is_active", "updated_at")}, "kind_label": KIND_LABEL[lic.kind], **service.status_of(lic)})


@router.get("/licences/kinds")
def kinds(access: View) -> list[dict[str, str]]:
    return [{"kind": k.value, "label": v} for k, v in KIND_LABEL.items()]


@router.get("/licences", response_model=list[LicenceOut])
def list_licences(access: View, db: DB) -> list[LicenceOut]:
    return [_out(x) for x in service.listing(db, access)]


@router.get("/licences/summary")
def summary(access: View, db: DB) -> dict[str, Any]:
    rows = [_out(x) for x in service.listing(db, access)]
    return {"total": sum(r.is_active for r in rows), "expired": sum(r.status == "expired" for r in rows),
            "due": sum(r.status == "due" for r in rows),
            "next": next((r.model_dump(include={"id", "name", "number", "expiry_date", "days_left"})
                          for r in rows if r.days_left is not None and r.days_left >= 0 and r.is_active), None)}


@router.post("/licences", response_model=LicenceOut, status_code=status.HTTP_201_CREATED)
def create_licence(body: LicenceIn, access: Manage, db: DB) -> LicenceOut:
    lic = service.create(db, access, body.model_dump())
    db.commit()
    return _out(lic)


@router.patch("/licences/{licence_id}", response_model=LicenceOut)
def update_licence(licence_id: uuid.UUID, body: LicenceUpdateIn, access: Manage, db: DB) -> LicenceOut:
    lic = service.update(db, access, service.get(db, access, licence_id), body.model_dump(exclude_unset=True))
    db.commit()
    return _out(lic)


@router.delete("/licences/{licence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_licence(licence_id: uuid.UUID, access: Manage, db: DB) -> None:
    service.delete(db, access, service.get(db, access, licence_id))
    db.commit()
