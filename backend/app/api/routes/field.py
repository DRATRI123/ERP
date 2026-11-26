"""Field sales: beats, the salesman's day, visits and targets."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import DB, require
from app.core.clock import local_today
from app.modules.access.service import BusinessAccess
from app.modules.field import service
from app.modules.field.models import Beat, VisitOutcome

router = APIRouter(prefix="/businesses/{business_id}/field", tags=["field sales"])
View = Annotated[BusinessAccess, Depends(require("customers.view"))]
Manage = Annotated[BusinessAccess, Depends(require("customers.view", "customers.manage"))]
Targets = Annotated[BusinessAccess, Depends(require("users.manage"))]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class BeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    weekday: int | None = Field(None, ge=0, le=6, description="0 = Monday; empty = any day")
    salesman_id: uuid.UUID | None = None
    party_ids: list[uuid.UUID] = Field(default_factory=list, max_length=300)
    notes: Annotated[str, StringConstraints(max_length=300)] | None = None
    is_active: bool = True


class BeatUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    weekday: int | None = Field(None, ge=0, le=6)
    salesman_id: uuid.UUID | None = None
    party_ids: list[uuid.UUID] | None = Field(None, max_length=300)
    notes: Annotated[str, StringConstraints(max_length=300)] | None = None
    is_active: bool | None = None


class BeatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    weekday: int | None
    salesman_id: uuid.UUID | None
    party_ids: list[str]
    notes: str | None
    is_active: bool


class VisitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    party_id: uuid.UUID
    outcome: VisitOutcome
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    beat_id: uuid.UUID | None = None
    visit_date: date = Field(default_factory=local_today)
    latitude: Annotated[Decimal, Field(ge=-90, le=90, decimal_places=6)] | None = None
    longitude: Annotated[Decimal, Field(ge=-180, le=180, decimal_places=6)] | None = None
    document_id: uuid.UUID | None = None
    payment_id: uuid.UUID | None = None
    next_visit: date | None = None


class TargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    salesman_id: uuid.UUID
    month: date
    amount: Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)]
    collection_amount: Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)] | None = None


@router.get("/salesmen")
def salesmen(access: View, db: DB) -> list[dict[str, Any]]:
    return service.salesmen(db, access)


@router.get("/beats", response_model=list[BeatOut])
def list_beats(access: View, db: DB) -> list[Beat]:
    return service.list_beats(db, access)


@router.post("/beats", response_model=BeatOut, status_code=status.HTTP_201_CREATED)
def create_beat(body: BeatIn, access: Manage, db: DB) -> Beat:
    b = service.save_beat(db, access, body.model_dump())
    db.commit()
    return b


@router.patch("/beats/{beat_id}", response_model=BeatOut)
def update_beat(beat_id: uuid.UUID, body: BeatUpdateIn, access: Manage, db: DB) -> Beat:
    b = service.save_beat(db, access, body.model_dump(exclude_unset=True), service.get_beat(db, access, beat_id))
    db.commit()
    return b


@router.get("/day")
def my_day(access: View, db: DB, day: date | None = None, salesman_id: uuid.UUID | None = None,
           beat_id: uuid.UUID | None = None) -> dict[str, Any]:
    """The salesman's beat for the day (by weekday), each customer with dues and today's visit."""
    return service.my_day(db, access, day=day or local_today(), salesman_id=salesman_id, beat_id=beat_id)


@router.get("/me")
def my_summary(access: View, db: DB) -> dict[str, Any]:
    """Salesperson portal home: today's orders, collections and visits, the month's target, recent work."""
    return service.my_summary(db, access, today=local_today())


@router.post("/visits", status_code=status.HTTP_201_CREATED)
def record_visit(body: VisitIn, access: View, db: DB) -> dict[str, Any]:
    v = service.record_visit(db, access, body.model_dump())
    db.commit()
    return {"id": v.id, "outcome": v.outcome.value, "visit_date": v.visit_date}


@router.get("/visits")
def list_visits(access: View, db: DB, date_from: date | None = None, date_to: date | None = None,
                salesman_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    to = date_to or local_today()
    return service.list_visits(db, access, date_from=date_from or to, date_to=to, salesman_id=salesman_id)


@router.get("/targets")
def targets(access: View, db: DB, month: date | None = None) -> dict[str, Any]:
    today = local_today()
    return service.targets(db, access, month=month or today, today=today)


@router.put("/targets")
def set_target(body: TargetIn, access: Targets, db: DB) -> dict[str, Any]:
    t = service.set_target(db, access, salesman_id=body.salesman_id, month=body.month, amount=body.amount,
                           collection_amount=body.collection_amount)
    db.commit()
    return {"id": t.id, "salesman_id": t.salesman_id, "month": t.month, "amount": t.amount}
