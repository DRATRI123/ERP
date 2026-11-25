"""Price lists (customer-type prices, quantity breaks) and trade schemes."""

from __future__ import annotations

import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, Quantity, str_enum
from app.modules.catalog.models import Rate


class PriceBase(enum.StrEnum):
    SELLING = "selling"
    WHOLESALE = "wholesale"
    MRP = "mrp"
    PURCHASE = "purchase"


class PriceList(UUIDPrimaryKey, Timestamps, Base):
    """E.g. "Hospitals: MRP − 15 %", "Doctors: selling price − 10 %", "Stockists: purchase + 8 %".
    Products listed in ``items`` use their fixed price instead (with optional quantity breaks)."""

    __tablename__ = "price_lists"
    __table_args__ = (UniqueConstraint("business_id", "name"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    base: Mapped[PriceBase] = mapped_column(str_enum(PriceBase, 12), default=PriceBase.SELLING, nullable=False)
    adjust_percent: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)  # −10 = 10 % off
    prices_include_tax: Mapped[bool | None] = mapped_column(Boolean)  # None = as the product says
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    items: Mapped[list[PriceListItem]] = relationship(cascade="all, delete-orphan", lazy="selectin",
                                                      order_by="(PriceListItem.product_id, PriceListItem.min_quantity)")


class PriceListItem(UUIDPrimaryKey, Base):
    __tablename__ = "price_list_items"
    __table_args__ = (UniqueConstraint("price_list_id", "product_id", "min_quantity"),
                      CheckConstraint("price >= 0", name="price_non_negative"),
                      CheckConstraint("min_quantity > 0", name="min_quantity_positive"))

    price_list_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("price_lists.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    min_quantity: Mapped[Decimal] = mapped_column(Quantity(), default=Decimal(1), nullable=False)
    price: Mapped[Decimal] = mapped_column(Money(), nullable=False)


class SchemeKind(enum.StrEnum):
    FREE_GOODS = "free_goods"  # buy min_quantity, get free_quantity free (repeats: 10+1, 20+2 …)
    DISCOUNT = "discount"  # discount_percent when buying at least min_quantity


class Scheme(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "schemes"
    __table_args__ = (Index("ix_schemes_business_active", "business_id", "is_active"),
                      CheckConstraint("min_quantity > 0", name="scheme_min_quantity_positive"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[SchemeKind] = mapped_column(str_enum(SchemeKind, 12), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"))
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("categories.id", ondelete="CASCADE"))
    min_quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    free_quantity: Mapped[Decimal] = mapped_column(Quantity(), default=Decimal(0), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Who pays for the scheme: the manufacturer / distributor it is claimed from, and the share they bear.
    claim_party_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="SET NULL"))
    claim_percent: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(100), nullable=False)
