"""Inventory: warehouses, batches, serials, stock documents and the perpetual stock ledger.

Design:

* ``stock_ledger`` is append-only. Every quantity change is one signed row carrying its value
  at cost and the running balance after it. Nothing is ever updated or deleted; a cancelled
  document is undone by posting reversing rows.
* ``stock_balances`` is the current quantity/value per (product, warehouse, batch). It is
  derived from the ledger and must always equal its sum (verified by tests and the
  ``verify_balances`` check).
* ``cost_layers`` hold remaining quantity per receipt for FIFO valuation;
  ``layer_consumptions`` remember exactly which layers an issue consumed, so reversals restore
  the original costs.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.db.types import Money, Quantity, UTCDateTime, str_enum

NO_BATCH = ""  # batch_key value for products without batch tracking


class Warehouse(UUIDPrimaryKey, Timestamps, Base):
    """A stock location. Belongs to a branch; branch access implies warehouse access."""

    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("business_id", "code"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Batch(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("product_id", "batch_no"),
        Index("ix_batches_business_expiry", "business_id", "expiry_date"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    batch_no: Mapped[str] = mapped_column(String(60), nullable=False)
    mfg_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)


class SerialStatus(enum.StrEnum):
    IN_STOCK = "in_stock"
    OUT = "out"


class SerialNumber(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "serial_numbers"
    __table_args__ = (UniqueConstraint("product_id", "serial_no"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    serial_no: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[SerialStatus] = mapped_column(str_enum(SerialStatus, 10), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )


class StockDocumentKind(enum.StrEnum):
    OPENING = "opening"
    ADJUSTMENT = "adjustment"
    TRANSFER = "transfer"
    COUNT = "count"


class StockDocumentStatus(enum.StrEnum):
    POSTED = "posted"
    CANCELLED = "cancelled"


class AdjustmentReason(enum.StrEnum):
    DAMAGE = "damage"
    EXPIRY = "expiry"
    THEFT = "theft"
    SAMPLE = "sample"
    INTERNAL_USE = "internal_use"
    CORRECTION = "correction"
    FOUND = "found"
    OTHER = "other"


class StockDocument(UUIDPrimaryKey, Timestamps, Base):
    """Header of an inventory-only document (opening stock, adjustment, transfer, count).

    Sales and purchase documents (Phase 3) post to the same ledger with their own source type.
    """

    __tablename__ = "stock_documents"
    __table_args__ = (
        UniqueConstraint("business_id", "number"),
        Index("ix_stock_documents_business_date", "business_id", "doc_date"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    financial_year_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("financial_years.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[StockDocumentKind] = mapped_column(str_enum(StockDocumentKind, 12), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    to_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT")
    )
    reason: Mapped[AdjustmentReason | None] = mapped_column(str_enum(AdjustmentReason, 16))
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[StockDocumentStatus] = mapped_column(
        str_enum(StockDocumentStatus, 10), default=StockDocumentStatus.POSTED, nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(300))

    lines: Mapped[list[StockDocumentLine]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="StockDocumentLine.line_no"
    )


class StockDocumentLine(UUIDPrimaryKey, Base):
    __tablename__ = "stock_document_lines"
    __table_args__ = (UniqueConstraint("document_id", "line_no"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_documents.id", ondelete="CASCADE"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    # Signed: positive = into stock, negative = out of stock (transfers: quantity moved).
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money())
    # Physical count only: what the system held and what was counted.
    system_quantity: Mapped[Decimal | None] = mapped_column(Quantity())
    counted_quantity: Mapped[Decimal | None] = mapped_column(Quantity())
    serials: Mapped[list[str] | None] = mapped_column(JSON)


class StockLedgerEntry(UUIDPrimaryKey, Base):
    __tablename__ = "stock_ledger"
    __table_args__ = (
        Index("ix_stock_ledger_product_wh_posted", "product_id", "warehouse_id", "posted_at"),
        Index("ix_stock_ledger_business_date", "business_id", "entry_date"),
        Index("ix_stock_ledger_source", "source_type", "source_id"),
        CheckConstraint("quantity <> 0", name="quantity_non_zero"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    # "stock_document" now; "sales_invoice", "purchase_invoice", … from Phase 3.
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)  # signed
    unit_cost: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    value: Mapped[Decimal] = mapped_column(Money(), nullable=False)  # signed, at cost
    balance_quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    balance_value: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_ledger.id", ondelete="RESTRICT"), unique=True
    )


class StockBalance(UUIDPrimaryKey, Base):
    __tablename__ = "stock_balances"
    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_id", "batch_key"),
        Index("ix_stock_balances_business_wh", "business_id", "warehouse_id"),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    # batch id as text, or "" when not batch-tracked: a NOT NULL key keeps the unique
    # constraint effective on every database (NULLs never conflict in unique constraints).
    batch_key: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    quantity: Mapped[Decimal] = mapped_column(Quantity(), default=Decimal(0), nullable=False)
    value: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class CostLayer(UUIDPrimaryKey, Base):
    """FIFO receipt layer: quantity still available at a given unit cost."""

    __tablename__ = "cost_layers"
    __table_args__ = (
        Index("ix_cost_layers_open", "product_id", "warehouse_id", "batch_key", "received_date"),
        CheckConstraint("remaining_quantity >= 0", name="remaining_non_negative"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    batch_key: Mapped[str] = mapped_column(String(36), nullable=False)
    source_entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_ledger.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    original_quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    remaining_quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    # Value still in the layer. Issuing the last unit takes exactly this, so per-unit rounding
    # never leaves residue in (or overdraws) the stock value.
    remaining_value: Mapped[Decimal] = mapped_column(Money(), nullable=False)


class LayerConsumption(UUIDPrimaryKey, Base):
    __tablename__ = "layer_consumptions"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_ledger.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    layer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cost_layers.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    value: Mapped[Decimal] = mapped_column(Money(), nullable=False)


class StockEntrySerial(Base):
    """Which serial numbers moved in a ledger entry (full serial history)."""

    __tablename__ = "stock_entry_serials"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_ledger.id", ondelete="RESTRICT"), primary_key=True
    )
    serial_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("serial_numbers.id", ondelete="RESTRICT"), primary_key=True
    )
