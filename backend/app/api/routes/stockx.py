"""Supplier bill import, damage / breakage claims and loyalty points."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import DB, require
from app.api.schemas_billing import DocumentOut, MoneyOut
from app.core.clock import local_today
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.catalog.models import Product
from app.modules.integrations.data_io import read_rows
from app.modules.parties.models import Party
from app.modules.stockx import claims, loyalty, purchase_import
from app.modules.stockx.models import ClaimStatus, DamageClaim, DamageReason

router = APIRouter(prefix="/businesses/{business_id}", tags=["stock"])
PurchaseCreate = Annotated[BusinessAccess, Depends(require("purchases.view", "purchases.create"))]
PurchaseView = Annotated[BusinessAccess, Depends(require("purchases.view"))]
StockView = Annotated[BusinessAccess, Depends(require("stock.view"))]
StockEdit = Annotated[BusinessAccess, Depends(require("stock.view", "stock.edit"))]
CustView = Annotated[BusinessAccess, Depends(require("customers.view"))]
CustManage = Annotated[BusinessAccess, Depends(require("customers.view", "customers.manage"))]
Settings = Annotated[BusinessAccess, Depends(require("business.settings"))]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
Qty = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=4)]


# ---------------------------------------------------------------------------- supplier bill import


class ImportRowIn(BaseModel):
    product_id: uuid.UUID
    quantity: Qty
    free: Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=3)] = Decimal(0)
    rate: Amount
    discount_percent: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)] = Decimal(0)
    gst_rate: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)] | None = None
    batch_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    expiry_date: date | None = None
    mrp: Amount | None = None
    supplier_text: Annotated[str, StringConstraints(max_length=200)] | None = None
    supplier_key: Annotated[str, StringConstraints(max_length=200)] | None = None


class ImportBillIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    party_id: uuid.UUID
    supplier_invoice_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    supplier_invoice_date: date | None = None
    doc_date: date = Field(default_factory=local_today)
    warehouse_id: uuid.UUID | None = None
    remember: bool = True
    rows: list[ImportRowIn] = Field(min_length=1, max_length=500)


@router.post("/purchases/import/preview")
async def import_preview(access: PurchaseCreate, db: DB, party_id: Annotated[uuid.UUID, Form()],
                         file: UploadFile = File(...)) -> dict[str, Any]:
    """Read the supplier's CSV / Excel bill: columns found, and each line matched to a product (or not)."""
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise ValidationFailed("Files up to 5 MB")
    return purchase_import.preview(db, access, party_id=party_id, rows=read_rows(file.filename or "bill.csv", data))


@router.post("/purchases/import", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def import_bill(body: ImportBillIn, access: PurchaseCreate, db: DB) -> Any:
    """Create the draft purchase bill; the supplier's item names are remembered for next time."""
    header = body.model_dump(include={"supplier_invoice_no", "supplier_invoice_date", "doc_date", "warehouse_id"},
                             exclude_none=True)
    doc = purchase_import.create_bill(db, access, party_id=body.party_id, rows=[r.model_dump() for r in body.rows],
                                      header=header, remember=body.remember)
    db.commit()
    return DocumentOut.model_validate(doc)


@router.get("/suppliers/{party_id}/item-names")
def item_names(party_id: uuid.UUID, access: PurchaseView, db: DB) -> list[dict[str, Any]]:
    return purchase_import.mappings(db, access, party_id)


@router.delete("/suppliers/{party_id}/item-names/{map_id}", status_code=status.HTTP_204_NO_CONTENT)
def forget_item_name(party_id: uuid.UUID, map_id: uuid.UUID, access: PurchaseCreate, db: DB) -> None:
    purchase_import.forget(db, access, party_id=party_id, map_id=map_id)
    db.commit()


# ---------------------------------------------------------------------------- damage claims


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    batch_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    quantity: Qty
    reason: DamageReason
    claim_date: date = Field(default_factory=local_today)
    party_id: uuid.UUID | None = Field(None, description="Supplier to claim from; empty = write off only")
    claim_amount: Amount | None = Field(None, description="Default: the stock value written off")
    notes: Annotated[str, StringConstraints(max_length=500)] | None = None
    file_id: uuid.UUID | None = None


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    number: str
    claim_date: date
    product_id: uuid.UUID
    product_name: str | None = None
    warehouse_id: uuid.UUID
    batch_no: str | None
    quantity: Decimal
    reason: DamageReason
    cost_value: MoneyOut
    party_id: uuid.UUID | None
    party_name: str | None = None
    claim_amount: MoneyOut
    settled_amount: MoneyOut
    status: ClaimStatus
    settlement_ref: str | None
    settled_on: date | None
    notes: str | None
    file_id: uuid.UUID | None
    stock_document_id: uuid.UUID


class SettleIn(BaseModel):
    amount: Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=2)]
    on: date = Field(default_factory=local_today)
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None


class RejectIn(BaseModel):
    reason: Reason


def _claim_out(db: DB, c: DamageClaim) -> ClaimOut:
    p, party = db.get(Product, c.product_id), db.get(Party, c.party_id) if c.party_id else None
    base = {k: getattr(c, k) for k in ClaimOut.model_fields if hasattr(c, k)}
    return ClaimOut.model_validate({**base, "product_name": p.name if p else None, "party_name": party.name if party else None})


@router.get("/damage-claims", response_model=list[ClaimOut])
def list_claims(access: StockView, db: DB, status_: ClaimStatus | None = Query(None, alias="status")) -> list[ClaimOut]:
    return [_claim_out(db, c) for c in claims.list_claims(db, access, status=status_)]


@router.get("/damage-claims/summary")
def claims_summary(access: StockView, db: DB) -> dict[str, Any]:
    return claims.summary(db, access)


@router.post("/damage-claims", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
def create_claim(body: ClaimIn, access: StockEdit, db: DB) -> ClaimOut:
    c = claims.create_claim(db, access, body.model_dump())
    db.commit()
    return _claim_out(db, c)


@router.post("/damage-claims/{claim_id}/settle", response_model=ClaimOut)
def settle_claim(claim_id: uuid.UUID, body: SettleIn, access: StockEdit, db: DB) -> ClaimOut:
    c = claims.settle_claim(db, access, claims.get_claim(db, access, claim_id), amount=body.amount, on=body.on,
                            reference=body.reference)
    db.commit()
    return _claim_out(db, c)


@router.post("/damage-claims/{claim_id}/reject", response_model=ClaimOut)
def reject_claim(claim_id: uuid.UUID, body: RejectIn, access: StockEdit, db: DB) -> ClaimOut:
    c = claims.reject_claim(db, access, claims.get_claim(db, access, claim_id), reason=body.reason)
    db.commit()
    return _claim_out(db, c)


# ---------------------------------------------------------------------------- loyalty


class LoyaltySettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    earn_per_100: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)] = Field(description="Points per ₹100 billed")
    point_value: Annotated[Decimal, Field(ge=0, le=1000, decimal_places=2)] = Field(description="₹ value of one point")
    min_redeem: Annotated[Decimal, Field(ge=0, le=1_000_000, decimal_places=0)] = Decimal(0)


class AdjustIn(BaseModel):
    points: Annotated[Decimal, Field(ge=-1_000_000, le=1_000_000, decimal_places=2)]
    note: Reason


@router.get("/loyalty")
def loyalty_overview(access: CustView, db: DB) -> dict[str, Any]:
    return loyalty.members(db, access)


@router.put("/loyalty/settings")
def loyalty_settings(body: LoyaltySettingsIn, access: Settings, db: DB) -> dict[str, Any]:
    business = access.business
    value = {k: str(v) for k, v in body.model_dump().items()}
    business.settings = {**(business.settings or {}), "loyalty": value}
    audit.record(db, action="business.settings_updated", entity_type="business", entity_id=business.id,
                 business_id=business.id, actor_id=access.user.id, changes={"loyalty": value})
    db.commit()
    return loyalty.settings_of(business)


@router.get("/customers/{party_id}/loyalty")
def customer_loyalty(party_id: uuid.UUID, access: CustView, db: DB) -> dict[str, Any]:
    return loyalty.statement(db, access, party_id)


@router.post("/customers/{party_id}/loyalty/adjust")
def adjust_loyalty(party_id: uuid.UUID, body: AdjustIn, access: CustManage, db: DB) -> dict[str, Any]:
    out = loyalty.adjust(db, access, party_id, body.points, body.note, local_today())
    db.commit()
    return out
