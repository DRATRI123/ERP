"""Schemas for products, units, categories, brands, parties and warehouses (Phase 2).

Decimals are accepted as JSON strings or numbers and always returned as strings, so no amount
ever passes through binary floating point on the way out.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.api.schemas import GSTIN, PAN, Name, Phone, StateCode
from app.modules.catalog.models import ProductKind
from app.modules.parties.models import AddressKind, PartyGstType

def scaled(places: int) -> AfterValidator:
    """Normalise to the column's scale so responses always show it (e.g. "12.0000")."""
    quantum = Decimal(1).scaleb(-places)
    return AfterValidator(lambda d: d.quantize(quantum))


MoneyIn = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4), scaled(4)]
RateIn = Annotated[Decimal, Field(ge=0, le=100, max_digits=9, decimal_places=4), scaled(4)]
QtyIn = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3), scaled(3)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True,
                                        pattern=r"^[A-Za-z0-9\-_./]{1,20}$")]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


def _hsn(v: str) -> str:
    v = v.strip()
    if not v.isdigit() or len(v) not in (4, 6, 8):
        raise ValueError("HSN/SAC must be 4, 6 or 8 digits")
    return v


HsnSac = Annotated[str, AfterValidator(_hsn)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------- units etc.


class UnitIn(BaseModel):
    code: Code
    name: Name
    uqc: Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Za-z]{3}$")] | None = None
    decimals: int = Field(0, ge=0, le=3)


class UnitUpdateIn(BaseModel):
    name: Name | None = None
    uqc: Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Za-z]{3}$")] | None = None
    decimals: int | None = Field(None, ge=0, le=3)
    is_active: bool | None = None


class UnitOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    uqc: str | None
    decimals: int
    is_active: bool


class CategoryIn(BaseModel):
    name: Name
    parent_id: uuid.UUID | None = None


class CategoryUpdateIn(BaseModel):
    name: Name | None = None
    parent_id: uuid.UUID | None = None
    is_active: bool | None = None


class CategoryOut(ORM):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    is_active: bool


class BrandIn(BaseModel):
    name: Name


class BrandUpdateIn(BaseModel):
    name: Name | None = None
    is_active: bool | None = None


class BrandOut(ORM):
    id: uuid.UUID
    name: str
    is_active: bool


# ---------------------------------------------------------------------------- products


class ProductBase(BaseModel):
    hsn_sac: HsnSac | None = None
    description: str | None = Field(None, max_length=5000)
    category_id: uuid.UUID | None = None
    brand_id: uuid.UUID | None = None
    barcode: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None
    purchase_price: MoneyIn | None = None
    selling_price: MoneyIn | None = None
    wholesale_price: MoneyIn | None = None
    mrp: MoneyIn | None = None
    reorder_level: QtyIn | None = None
    reorder_quantity: QtyIn | None = None


class ProductIn(ProductBase):
    kind: ProductKind = ProductKind.GOODS
    name: Name
    sku: Sku
    unit_id: uuid.UUID
    prices_include_tax: bool = False
    gst_rate: RateIn = Decimal("0")
    cess_rate: RateIn = Decimal("0")
    track_inventory: bool = True
    batch_tracking: bool = False
    serial_tracking: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProductUpdateIn(ProductBase):
    kind: ProductKind | None = None
    name: Name | None = None
    sku: Sku | None = None
    unit_id: uuid.UUID | None = None
    prices_include_tax: bool | None = None
    gst_rate: RateIn | None = None
    cess_rate: RateIn | None = None
    track_inventory: bool | None = None
    batch_tracking: bool | None = None
    serial_tracking: bool | None = None
    attributes: dict[str, Any] | None = None


class ProductOut(ORM):
    id: uuid.UUID
    kind: ProductKind
    name: str
    sku: str
    barcode: str | None
    hsn_sac: str | None
    description: str | None
    category_id: uuid.UUID | None
    brand_id: uuid.UUID | None
    unit_id: uuid.UUID
    purchase_price: Decimal | None = Field(
        description="Hidden (null) unless the caller has stock_value.view")
    selling_price: Decimal | None
    wholesale_price: Decimal | None
    mrp: Decimal | None
    prices_include_tax: bool
    gst_rate: Decimal
    cess_rate: Decimal
    track_inventory: bool
    batch_tracking: bool
    serial_tracking: bool
    reorder_level: Decimal | None
    reorder_quantity: Decimal | None
    attributes: dict[str, Any]
    is_active: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    on_hand: Decimal | None = Field(None, description="Total quantity in warehouses you can access")


# ---------------------------------------------------------------------------- parties


class GroupIn(BaseModel):
    name: Name


class GroupOut(ORM):
    id: uuid.UUID
    name: str


class AddressIn(BaseModel):
    kind: AddressKind
    label: Annotated[str, StringConstraints(max_length=60)] | None = None
    address_line1: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    address_line2: Annotated[str, StringConstraints(max_length=200)] | None = None
    city: Annotated[str, StringConstraints(max_length=100)] | None = None
    state_code: StateCode | None = None
    pincode: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")] | None = None
    country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] = "IN"
    is_default: bool = False


class AddressUpdateIn(BaseModel):
    label: Annotated[str, StringConstraints(max_length=60)] | None = None
    address_line1: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    address_line2: Annotated[str, StringConstraints(max_length=200)] | None = None
    city: Annotated[str, StringConstraints(max_length=100)] | None = None
    state_code: StateCode | None = None
    pincode: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")] | None = None
    is_default: bool | None = None


class AddressOut(ORM):
    id: uuid.UUID
    kind: AddressKind
    label: str | None
    address_line1: str
    address_line2: str | None
    city: str | None
    state_code: str | None
    pincode: str | None
    country: str
    is_default: bool


class PartyCommon(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, max_length=30)] | None = None
    gstin: GSTIN | None = None
    pan: PAN | None = None
    state_code: StateCode | None = None
    contact_person: Annotated[str, StringConstraints(max_length=120)] | None = None
    phone: Phone | None = None
    email: EmailStr | None = None
    customer_group_id: uuid.UUID | None = None
    supplier_group_id: uuid.UUID | None = None
    credit_limit: MoneyIn | None = None
    credit_days: int | None = Field(None, ge=0, le=3650)
    notes: str | None = Field(None, max_length=5000)


class PartyIn(PartyCommon):
    name: Name
    gst_type: PartyGstType = PartyGstType.UNREGISTERED
    is_customer: bool = False
    is_supplier: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)
    addresses: list[AddressIn] = Field(default_factory=list, max_length=20)


class PartyUpdateIn(PartyCommon):
    name: Name | None = None
    gst_type: PartyGstType | None = None
    is_customer: bool | None = None
    is_supplier: bool | None = None
    attributes: dict[str, Any] | None = None


class PartyOut(ORM):
    id: uuid.UUID
    code: str | None
    name: str
    is_customer: bool
    is_supplier: bool
    gst_type: PartyGstType
    gstin: str | None
    pan: str | None
    state_code: str | None
    contact_person: str | None
    phone: str | None
    email: str | None
    customer_group_id: uuid.UUID | None
    supplier_group_id: uuid.UUID | None
    credit_limit: Decimal | None
    credit_days: int | None
    notes: str | None
    attributes: dict[str, Any]
    is_active: bool
    archived_at: datetime | None
    addresses: list[AddressOut]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------- warehouses


class WarehouseIn(BaseModel):
    branch_id: uuid.UUID
    code: Code
    name: Name
    address: Annotated[str, StringConstraints(max_length=300)] | None = None


class WarehouseUpdateIn(BaseModel):
    branch_id: uuid.UUID | None = None
    code: Code | None = None
    name: Name | None = None
    address: Annotated[str, StringConstraints(max_length=300)] | None = None
    is_active: bool | None = None


class WarehouseOut(ORM):
    id: uuid.UUID
    branch_id: uuid.UUID
    code: str
    name: str
    address: str | None
    is_active: bool
