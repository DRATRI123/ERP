"""Product master data: units, categories, brands and products."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, Quantity, ScaledDecimal, UTCDateTime, str_enum


def Rate() -> ScaledDecimal:  # noqa: N802
    """Percentage rate such as 18.0000 (GST) or 0.2500 (special rates)."""
    return ScaledDecimal(9, 4)


class Unit(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("business_id", "code"),
        CheckConstraint("decimals BETWEEN 0 AND 3", name="decimals_range"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)  # e.g. NOS, KG, BTL
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    # GST Unique Quantity Code used in returns (e.g. NOS, KGS, LTR, BTL).
    uqc: Mapped[str | None] = mapped_column(String(8))
    # Quantity decimals allowed: 0 for pieces, 3 for kg/litre.
    decimals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Category(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "categories"

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("categories.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Brand(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("business_id", "name"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProductKind(enum.StrEnum):
    GOODS = "goods"
    SERVICE = "service"


class Product(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("business_id", "sku"),
        # Barcodes are optional; NULLs never collide, real barcodes are unique per business.
        UniqueConstraint("business_id", "barcode"),
        Index("ix_products_business_name", "business_id", "name"),
        CheckConstraint("gst_rate >= 0", name="gst_rate_non_negative"),
        CheckConstraint("cess_rate >= 0", name="cess_rate_non_negative"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[ProductKind] = mapped_column(
        str_enum(ProductKind, 10), default=ProductKind.GOODS, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(64))
    hsn_sac: Mapped[str | None] = mapped_column(String(8))
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("categories.id", ondelete="RESTRICT"), index=True
    )
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("brands.id", ondelete="RESTRICT"), index=True
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )

    purchase_price: Mapped[Decimal | None] = mapped_column(Money())
    selling_price: Mapped[Decimal | None] = mapped_column(Money())  # retail price
    wholesale_price: Mapped[Decimal | None] = mapped_column(Money())
    mrp: Mapped[Decimal | None] = mapped_column(Money())
    prices_include_tax: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Rate(), default=Decimal("0"), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Rate(), default=Decimal("0"), nullable=False)

    track_inventory: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    batch_tracking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    serial_tracking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reorder_level: Mapped[Decimal | None] = mapped_column(Quantity())
    reorder_quantity: Mapped[Decimal | None] = mapped_column(Quantity())

    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
