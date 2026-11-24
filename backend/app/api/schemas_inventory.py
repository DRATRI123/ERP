"""Schemas for inventory documents and reports."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.schemas_masters import scaled

from app.modules.inventory.models import (
    AdjustmentReason,
    SerialStatus,
    StockDocumentKind,
    StockDocumentStatus,
)

Qty = Annotated[Decimal, Field(max_digits=18, decimal_places=3), scaled(3)]
PosQty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3), scaled(3)]
Cost = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4), scaled(4)]
BatchNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Serial = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Ref = Annotated[str, StringConstraints(max_length=100)]


class _Line(BaseModel):
    product_id: uuid.UUID
    batch_no: BatchNo | None = None


class OpeningLineIn(_Line):
    quantity: PosQty
    unit_cost: Cost
    mfg_date: date | None = None
    expiry_date: date | None = None
    serials: list[Serial] = Field(default_factory=list, max_length=10000)


class AdjustmentLineIn(_Line):
    quantity: Qty = Field(description="Positive adds stock, negative removes it")
    unit_cost: Cost | None = Field(
        None, description="For additions; defaults to the current average cost")
    mfg_date: date | None = None
    expiry_date: date | None = None
    serials: list[Serial] = Field(default_factory=list, max_length=10000)


class TransferLineIn(_Line):
    quantity: PosQty
    serials: list[Serial] = Field(default_factory=list, max_length=10000)


class CountLineIn(_Line):
    counted_quantity: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3), scaled(3)]
    unit_cost: Cost | None = None
    mfg_date: date | None = None
    expiry_date: date | None = None


class _Doc(BaseModel):
    doc_date: date
    reference: Ref | None = None
    notes: str | None = Field(None, max_length=2000)


class OpeningIn(_Doc):
    warehouse_id: uuid.UUID
    lines: list[OpeningLineIn] = Field(min_length=1, max_length=1000)


class AdjustmentIn(_Doc):
    warehouse_id: uuid.UUID
    reason: AdjustmentReason
    lines: list[AdjustmentLineIn] = Field(min_length=1, max_length=1000)


class TransferIn(_Doc):
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    lines: list[TransferLineIn] = Field(min_length=1, max_length=1000)


class CountIn(_Doc):
    warehouse_id: uuid.UUID
    lines: list[CountLineIn] = Field(min_length=1, max_length=5000)


class CancelIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    cancel_date: date | None = Field(None, description="Defaults to today")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentLineOut(ORM):
    line_no: int
    product_id: uuid.UUID
    batch_id: uuid.UUID | None
    quantity: Decimal
    unit_cost: Decimal | None
    system_quantity: Decimal | None
    counted_quantity: Decimal | None
    serials: list[str] | None


class LedgerEntryOut(ORM):
    id: uuid.UUID
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    batch_id: uuid.UUID | None
    entry_date: date
    posted_at: datetime
    source_type: str
    source_id: uuid.UUID
    quantity: Decimal
    balance_quantity: Decimal
    unit_cost: Decimal | None
    value: Decimal | None
    balance_value: Decimal | None
    reverses_entry_id: uuid.UUID | None


class DocumentOut(ORM):
    id: uuid.UUID
    kind: StockDocumentKind
    number: str
    doc_date: date
    warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID | None
    reason: AdjustmentReason | None
    reference: str | None
    notes: str | None
    status: StockDocumentStatus
    created_by_id: uuid.UUID
    created_at: datetime
    cancelled_at: datetime | None
    cancel_reason: str | None
    lines: list[DocumentLineOut]
    entries: list[LedgerEntryOut] = []


class DocumentSummaryOut(ORM):
    id: uuid.UUID
    kind: StockDocumentKind
    number: str
    doc_date: date
    warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID | None
    status: StockDocumentStatus
    reference: str | None
    created_at: datetime


class BalanceOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    batch_id: uuid.UUID | None
    batch_no: str | None
    expiry_date: date | None
    quantity: Decimal
    value: Decimal | None
    average_cost: Decimal | None


class LowStockOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    on_hand: Decimal
    reorder_level: Decimal | None
    reorder_quantity: Decimal | None


class WarehouseValueOut(BaseModel):
    warehouse_id: uuid.UUID
    code: str
    name: str
    stock_lines: int
    value: Decimal


class ValuationOut(BaseModel):
    method: str
    warehouses: list[WarehouseValueOut]
    total_value: Decimal


class BatchOut(ORM):
    id: uuid.UUID
    product_id: uuid.UUID
    batch_no: str
    mfg_date: date | None
    expiry_date: date | None


class SerialOut(ORM):
    id: uuid.UUID
    product_id: uuid.UUID
    serial_no: str
    status: SerialStatus
    warehouse_id: uuid.UUID | None
    batch_id: uuid.UUID | None
