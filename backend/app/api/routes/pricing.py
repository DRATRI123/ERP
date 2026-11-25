"""Price lists and schemes."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select

from app.api.deps import DB, require
from app.modules.access.service import BusinessAccess
from app.modules.catalog.models import Product
from app.modules.parties.models import Party
from app.modules.pricing import service
from app.modules.pricing.models import PriceBase, PriceList, Scheme, SchemeKind

router = APIRouter(prefix="/businesses/{business_id}", tags=["pricing"])
View = Annotated[BusinessAccess, Depends(require("products.view"))]
Manage = Annotated[BusinessAccess, Depends(require("products.view", "products.manage"))]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ItemIn(BaseModel):
    product_id: uuid.UUID
    min_quantity: Decimal = Field(Decimal(1), gt=0, max_digits=14, decimal_places=3)
    price: Decimal = Field(ge=0, max_digits=16, decimal_places=4)


class PriceListIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    base: PriceBase = PriceBase.SELLING
    adjust_percent: Decimal = Field(Decimal(0), max_digits=8, decimal_places=4)
    prices_include_tax: bool | None = None
    is_active: bool = True
    items: list[ItemIn] = Field(default_factory=list, max_length=5000)


class PriceListUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] | None = None
    base: PriceBase | None = None
    adjust_percent: Decimal | None = Field(None, max_digits=8, decimal_places=4)
    prices_include_tax: bool | None = None
    is_active: bool | None = None
    items: list[ItemIn] | None = Field(None, max_length=5000)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: uuid.UUID
    product_name: str | None = None
    min_quantity: Decimal
    price: Decimal


class PriceListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    base: PriceBase
    adjust_percent: Decimal
    prices_include_tax: bool | None
    is_active: bool
    items: list[ItemOut]
    customers: int = 0
    updated_at: datetime


class SchemeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    kind: SchemeKind
    product_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    min_quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    free_quantity: Decimal = Field(Decimal(0), ge=0, max_digits=14, decimal_places=3)
    discount_percent: Decimal = Field(Decimal(0), ge=0, le=100, max_digits=8, decimal_places=4)
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool = True
    claim_party_id: uuid.UUID | None = Field(None, description="Manufacturer / distributor who reimburses the scheme")
    claim_percent: Decimal = Field(Decimal(100), ge=0, le=100, max_digits=8, decimal_places=4)


class SchemeOut(SchemeIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_name: str | None = None
    claim_party_name: str | None = None
    updated_at: datetime


def _names(db: DB, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return dict(db.execute(select(Product.id, Product.name).where(Product.id.in_(ids))).all())


def _pl_out(db: DB, pl: PriceList) -> PriceListOut:
    n = db.scalar(select(func.count()).select_from(Party).where(Party.price_list_id == pl.id)) or 0
    names = _names(db, {i.product_id for i in pl.items})
    data = PriceListOut.model_validate(pl, from_attributes=True).model_dump(exclude={"customers"})
    for it in data["items"]:
        it["product_name"] = names.get(it["product_id"])
    return PriceListOut.model_validate({**data, "customers": n})


def _scheme_out(db: DB, s: Scheme) -> SchemeOut:
    out = SchemeOut.model_validate(s, from_attributes=True)
    out.product_name = _names(db, {s.product_id}).get(s.product_id) if s.product_id else None
    if s.claim_party_id:
        party = db.get(Party, s.claim_party_id)
        out.claim_party_name = party.name if party else None
    return out


@router.get("/price-lists", response_model=list[PriceListOut])
def list_price_lists(access: View, db: DB) -> list[PriceListOut]:
    rows = db.scalars(select(PriceList).where(PriceList.business_id == access.business.id).order_by(PriceList.name))
    return [_pl_out(db, pl) for pl in rows]


@router.post("/price-lists", response_model=PriceListOut, status_code=status.HTTP_201_CREATED)
def create_price_list(body: PriceListIn, access: Manage, db: DB) -> PriceListOut:
    pl = service.save_price_list(db, access, body.model_dump())
    db.commit()
    return _pl_out(db, pl)


@router.patch("/price-lists/{price_list_id}", response_model=PriceListOut)
def update_price_list(price_list_id: uuid.UUID, body: PriceListUpdateIn, access: Manage, db: DB) -> PriceListOut:
    pl = service.save_price_list(db, access, body.model_dump(exclude_unset=True),
                                 service.get_price_list(db, access, price_list_id))
    db.commit()
    return _pl_out(db, pl)


@router.delete("/price-lists/{price_list_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_price_list(price_list_id: uuid.UUID, access: Manage, db: DB) -> None:
    service.delete_price_list(db, access, service.get_price_list(db, access, price_list_id))
    db.commit()


@router.get("/schemes", response_model=list[SchemeOut])
def list_schemes(access: View, db: DB) -> list[SchemeOut]:
    rows = list(db.scalars(select(Scheme).where(Scheme.business_id == access.business.id)
                           .order_by(Scheme.is_active.desc(), Scheme.name)))
    names = _names(db, {s.product_id for s in rows if s.product_id})
    out = [SchemeOut.model_validate(s, from_attributes=True) for s in rows]
    for o in out:
        o.product_name = names.get(o.product_id) if o.product_id else None
    return out


@router.post("/schemes", response_model=SchemeOut, status_code=status.HTTP_201_CREATED)
def create_scheme(body: SchemeIn, access: Manage, db: DB) -> SchemeOut:
    s = service.save_scheme(db, access, body.model_dump())
    db.commit()
    return _scheme_out(db, s)


@router.put("/schemes/{scheme_id}", response_model=SchemeOut)
def update_scheme(scheme_id: uuid.UUID, body: SchemeIn, access: Manage, db: DB) -> SchemeOut:
    s = service.save_scheme(db, access, body.model_dump(), service.get_scheme(db, access, scheme_id))
    db.commit()
    return _scheme_out(db, s)


@router.delete("/schemes/{scheme_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scheme(scheme_id: uuid.UUID, access: Manage, db: DB) -> None:
    s = service.get_scheme(db, access, scheme_id)
    db.delete(s)
    db.commit()


@router.get("/parties/{party_id}/credit")
def credit_status(party_id: uuid.UUID, access: Annotated[BusinessAccess, Depends(require("customers.view"))],
                  db: DB) -> dict[str, Any]:
    """What billing shows when a customer is picked: limit, dues, overdue, hold and price list."""
    from app.core.clock import local_today
    from app.core.errors import NotFound
    from app.modules.billing.doctypes import DocType, Side
    from app.modules.billing.models import DocStatus, Document
    from app.modules.billing.service import party_outstanding

    p = db.get(Party, party_id)
    if p is None or p.business_id != access.business.id:
        raise NotFound("Customer not found")
    today = local_today()
    overdue = db.execute(select(func.coalesce(func.sum(Document.balance_due), 0), func.min(Document.due_date)).where(
        Document.party_id == p.id, Document.doc_type == DocType.SALES_INVOICE, Document.status == DocStatus.FINAL,
        Document.balance_due > 0, Document.due_date < today)).one()
    outstanding = party_outstanding(db, p.id, Side.SALES)
    pl = db.get(PriceList, p.price_list_id) if p.price_list_id else None
    return {"credit_limit": p.credit_limit, "outstanding": outstanding,
            "available": (p.credit_limit - outstanding) if p.credit_limit is not None else None,
            "overdue": overdue[0], "oldest_overdue_days": (today - overdue[1]).days if overdue[1] else None,
            "credit_hold": p.credit_hold, "credit_days": p.credit_days,
            "price_list": {"id": pl.id, "name": pl.name} if pl else None}
