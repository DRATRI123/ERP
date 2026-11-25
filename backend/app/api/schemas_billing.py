"""Schemas for billing documents, payments, tax rates, numbering series and templates."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
)

from app.api.schemas_masters import HsnSac, MoneyIn, RateIn, scaled
from app.core.clock import local_today
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import (
    DocStatus,
    PaymentDirection,
    PaymentMode,
    PaymentState,
    PaymentStatus,
    PriceTier,
    TaxMode,
)
from app.modules.billing.printing import TemplateConfig
from app.modules.gst.gstin import STATE_CODES
from app.modules.gst.tax import TaxCategory

PosQty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3), scaled(3)]
# Output types: always the column's full scale ("540.0000"), whether the value was just
# computed or read back from the database.
MoneyOut = Annotated[Decimal, PlainSerializer(lambda d: f"{d.quantize(Decimal('0.0001'))}", return_type=str)]
QtyOut = Annotated[Decimal, PlainSerializer(lambda d: f"{d.quantize(Decimal('0.001'))}", return_type=str)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


def _pos(v: str) -> str:
    v = v.strip().zfill(2)
    if v != "96" and v not in STATE_CODES:  # 96 = outside India (exports)
        raise ValueError(f"{v} is not a valid place-of-supply code")
    return v


PlaceOfSupply = Annotated[str, AfterValidator(_pos)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LineIn(BaseModel):
    product_id: uuid.UUID | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    hsn_sac: HsnSac | None = None
    quantity: PosQty = Decimal("1.000")
    unit_price: MoneyIn | None = Field(None, description="Defaults to the product's price for the tier")
    discount_percent: RateIn = Decimal("0.0000")
    discount_amount: MoneyIn = Decimal("0.0000")
    price_includes_tax: bool | None = None
    tax_category: TaxCategory | None = None
    gst_rate: RateIn | None = Field(None, description="Override; default is the HSN rule or product rate")
    cess_rate: RateIn | None = None
    itc_eligible: bool = True
    batch_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    expiry_date: date | None = None
    mrp: MoneyIn | None = Field(None, description="Purchases: MRP printed on this batch")
    serials: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]] | None = None
    source_line_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None


class _Header(BaseModel):
    branch_id: uuid.UUID | None = None
    series_id: uuid.UUID | None = None
    due_date: date | None = None
    party_id: uuid.UUID | None = None
    party_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    place_of_supply: PlaceOfSupply | None = None
    billing_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    reverse_charge: bool | None = None
    tax_mode: TaxMode | None = None
    price_tier: PriceTier | None = None
    prices_include_tax: bool | None = None
    warehouse_id: uuid.UUID | None = None
    restock: bool | None = None
    reference_document_id: uuid.UUID | None = None
    supplier_invoice_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    supplier_invoice_date: date | None = None
    landed_cost: MoneyIn | None = None
    salesperson_id: uuid.UUID | None = None
    doctor_id: uuid.UUID | None = None
    patient_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    patient_address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    rx_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    notes: Text | None = None
    terms: Text | None = None
    custom_fields: dict[str, Any] | None = None


class DocumentIn(_Header):
    doc_type: DocType
    doc_date: date = Field(default_factory=local_today)
    client_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] | None = Field(
        None, description="Your own id for this document; repeating a create with it is idempotent")
    lines: list[LineIn] = Field(min_length=1, max_length=500)


class DocumentUpdateIn(_Header):
    doc_date: date | None = None
    lines: list[LineIn] | None = Field(None, min_length=1, max_length=500)


class CounterPaymentIn(BaseModel):
    mode: PaymentMode
    amount: MoneyIn
    reference: Annotated[str, StringConstraints(max_length=100)] | None = None
    account_id: uuid.UUID | None = Field(None, description="Cash/bank account; default by mode")


class FinalizeIn(BaseModel):
    payments: list[CounterPaymentIn] = Field(default_factory=list, max_length=5)


class ConvertIn(BaseModel):
    target: DocType
    doc_date: date | None = None


class CancelIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    cancel_date: date | None = None


class IrnIn(BaseModel):
    irn: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    ack_no: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,30}$")]
    ack_date: datetime
    signed_qr: Annotated[str, StringConstraints(max_length=5000)] | None = None


class LineOut(ORM):
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID | None
    description: str
    hsn_sac: str | None
    unit_code: str | None
    is_service: bool
    quantity: QtyOut
    unit_price: MoneyOut
    mrp: MoneyOut | None
    discount_percent: MoneyOut
    discount_amount: MoneyOut
    price_includes_tax: bool
    tax_category: TaxCategory
    gst_rate: MoneyOut
    cess_rate: MoneyOut
    itc_eligible: bool
    gross_amount: MoneyOut
    discount_total: MoneyOut
    taxable_value: MoneyOut
    cgst_amount: MoneyOut
    sgst_amount: MoneyOut
    igst_amount: MoneyOut
    cess_amount: MoneyOut
    line_total: MoneyOut
    batch_no: str | None
    expiry_date: date | None
    serials: list[str] | None
    source_line_id: uuid.UUID | None
    custom_fields: dict[str, Any]


class DocumentSummaryOut(ORM):
    id: uuid.UUID
    doc_type: DocType
    status: DocStatus
    number: str | None
    doc_date: date
    due_date: date | None
    branch_id: uuid.UUID
    party_id: uuid.UUID | None
    party_name: str | None
    grand_total: MoneyOut
    balance_due: MoneyOut
    payment_status: PaymentStatus
    client_ref: str | None
    created_at: datetime


class DocumentOut(DocumentSummaryOut):
    series_id: uuid.UUID | None
    place_of_supply: str | None
    seller_gstin: str | None
    seller_state: str | None
    tax_mode: TaxMode
    is_interstate: bool
    is_utgst: bool
    reverse_charge: bool
    price_tier: PriceTier
    prices_include_tax: bool
    warehouse_id: uuid.UUID | None
    restock: bool
    reference_document_id: uuid.UUID | None
    supplier_invoice_no: str | None
    supplier_invoice_date: date | None
    landed_cost: MoneyOut
    salesperson_id: uuid.UUID | None
    doctor_id: uuid.UUID | None = None
    doctor_name: str | None = None
    patient_name: str | None = None
    patient_address: str | None = None
    rx_number: str | None = None
    billing_address_id: uuid.UUID | None
    shipping_address_id: uuid.UUID | None
    party_snapshot: dict[str, Any] | None
    seller_snapshot: dict[str, Any] | None
    subtotal: MoneyOut
    discount_total: MoneyOut
    taxable_total: MoneyOut
    cgst_total: MoneyOut
    sgst_total: MoneyOut
    igst_total: MoneyOut
    cess_total: MoneyOut
    tax_total: MoneyOut
    round_off: MoneyOut
    amount_payable: MoneyOut
    amount_paid: MoneyOut
    notes: str | None
    terms: str | None
    custom_fields: dict[str, Any]
    irn: str | None
    irn_ack_no: str | None
    irn_ack_date: datetime | None
    eway_bill_no: str | None = None
    eway_bill_date: datetime | None = None
    eway_valid_until: datetime | None = None
    vehicle_no: str | None = None
    transport_distance_km: int | None = None
    finalized_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    lines: list[LineOut]


class AllocationIn(BaseModel):
    document_id: uuid.UUID
    amount: MoneyIn


class PaymentIn(BaseModel):
    direction: PaymentDirection
    party_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None
    payment_date: date = Field(default_factory=local_today)
    mode: PaymentMode
    account_id: uuid.UUID | None = Field(None, description="Cash/bank account; default by mode")
    amount: MoneyIn
    reference: Annotated[str, StringConstraints(max_length=100)] | None = None
    notes: Annotated[str, StringConstraints(max_length=500)] | None = None
    allocations: list[AllocationIn] = Field(default_factory=list, max_length=200)
    tds_amount: MoneyIn = Field(Decimal(0), description="TDS deducted; amount + tds_amount settles the bills")
    tds_section: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{3}[A-Z0-9()]{0,6}$")] | None = None


class AllocationOut(ORM):
    document_id: uuid.UUID
    amount: MoneyOut


class PaymentOut(ORM):
    id: uuid.UUID
    direction: PaymentDirection
    number: str
    payment_date: date
    party_id: uuid.UUID | None
    branch_id: uuid.UUID
    mode: PaymentMode
    account_id: uuid.UUID | None
    amount: MoneyOut
    reference: str | None
    notes: str | None
    tds_amount: MoneyOut = Decimal(0)
    tds_section: str | None = None
    status: PaymentState
    allocations: list[AllocationOut]
    created_at: datetime
    cancelled_at: datetime | None
    cancel_reason: str | None


class PaymentCancelIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


class OutstandingOut(BaseModel):
    party_id: uuid.UUID
    side: str
    outstanding: MoneyOut
    overdue: MoneyOut
    documents: list[DocumentSummaryOut]


class TaxRateIn(BaseModel):
    hsn_prefix: Annotated[str, StringConstraints(pattern=r"^[0-9]{2,8}$")]
    gst_rate: RateIn
    cess_rate: RateIn = Decimal("0.0000")
    effective_from: date
    effective_to: date | None = None
    description: Annotated[str, StringConstraints(max_length=200)] | None = None


class TaxRateUpdateIn(BaseModel):
    effective_to: date | None = None
    description: Annotated[str, StringConstraints(max_length=200)] | None = None


class TaxRateOut(ORM):
    id: uuid.UUID
    hsn_prefix: str
    gst_rate: Decimal
    cess_rate: Decimal
    effective_from: date
    effective_to: date | None
    description: str | None


class SeriesIn(BaseModel):
    doc_type: DocType
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    prefix: Annotated[str, StringConstraints(max_length=30, pattern=r"^[A-Za-z0-9/\-{}]*$")]
    suffix: Annotated[str, StringConstraints(max_length=20, pattern=r"^[A-Za-z0-9/\-{}]*$")] = ""
    padding: int = Field(4, ge=1, le=8)
    branch_id: uuid.UUID | None = None
    is_default: bool = False


class SeriesUpdateIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    prefix: Annotated[str, StringConstraints(max_length=30, pattern=r"^[A-Za-z0-9/\-{}]*$")] | None = None
    suffix: Annotated[str, StringConstraints(max_length=20, pattern=r"^[A-Za-z0-9/\-{}]*$")] | None = None
    padding: int | None = Field(None, ge=1, le=8)
    is_default: bool | None = None
    is_active: bool | None = None


class SeriesOut(ORM):
    id: uuid.UUID
    doc_type: DocType
    name: str
    prefix: str
    suffix: str
    padding: int
    branch_id: uuid.UUID | None
    is_default: bool
    is_active: bool


class TemplateIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    branch_id: uuid.UUID | None = None
    doc_types: list[DocType] = Field(default_factory=list, description="Empty = all types")
    config: TemplateConfig = Field(default_factory=TemplateConfig)
    is_default: bool = False


class TemplateUpdateIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    doc_types: list[DocType] | None = None
    config: TemplateConfig | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class TemplateOut(ORM):
    id: uuid.UUID
    name: str
    branch_id: uuid.UUID | None
    doc_types: list[str]
    config: dict[str, Any]
    is_default: bool
    is_active: bool
