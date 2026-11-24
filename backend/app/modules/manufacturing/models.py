"""Manufacturing: recipes (bills of materials) and production runs."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, Quantity, UTCDateTime, str_enum
from app.modules.catalog.models import Rate


class Recipe(UUIDPrimaryKey, Timestamps, Base):
    """How to make ``output_quantity`` of a product, e.g. 10 kg of Ashwagandha Churna."""

    __tablename__ = "recipes"
    __table_args__ = (UniqueConstraint("business_id", "name"), CheckConstraint("output_quantity > 0", name="recipe_output_positive"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    output_quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    overhead: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)  # labour, fuel, packing per batch
    requires_qc: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shelf_life_days: Mapped[int | None] = mapped_column()  # expiry = manufacture date + this
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list[RecipeLine]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="RecipeLine.line_no")


class RecipeLine(UUIDPrimaryKey, Base):
    __tablename__ = "recipe_lines"
    __table_args__ = (UniqueConstraint("recipe_id", "line_no"), CheckConstraint("quantity > 0", name="recipe_line_qty_positive"))

    recipe_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False)
    line_no: Mapped[int] = mapped_column(nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)  # per output_quantity
    wastage_percent: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)


class QcStatus(enum.StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"  # finished batch on hold: cannot be sold
    RELEASED = "released"
    REJECTED = "rejected"  # written off


class ProductionStatus(enum.StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ProductionRun(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "production_runs"
    __table_args__ = (Index("ix_production_runs_business_date", "business_id", "doc_date"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    recipe_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("recipes.id", ondelete="RESTRICT"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    stock_document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_documents.id", ondelete="RESTRICT"), nullable=False)
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    batch_no: Mapped[str | None] = mapped_column(String(60))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    material_cost: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    overhead: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    status: Mapped[ProductionStatus] = mapped_column(str_enum(ProductionStatus, 10), nullable=False)
    qc_status: Mapped[QcStatus] = mapped_column(str_enum(QcStatus, 14), nullable=False)
    qc_notes: Mapped[str | None] = mapped_column(Text)
    qc_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    qc_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
