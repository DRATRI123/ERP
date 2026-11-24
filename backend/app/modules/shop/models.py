"""Public order catalogue shared on WhatsApp."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.db.types import Money, UTCDateTime


class Catalogue(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "catalogues"

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, unique=True)
    token: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)  # the secret in the link
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    welcome: Mapped[str | None] = mapped_column(Text)
    whatsapp_number: Mapped[str | None] = mapped_column(String(15))  # with country code, digits only
    category_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # empty = every category
    show_stock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # "in stock" / "out of stock" only
    min_order_value: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("branches.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class CatalogueOrder(UUIDPrimaryKey, Base):
    __tablename__ = "catalogue_orders"
    __table_args__ = (Index("ix_catalogue_orders_business_created", "business_id", "created_at"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    party_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(15), nullable=False)
    address: Mapped[str | None] = mapped_column(String(300))
    note: Mapped[str | None] = mapped_column(String(500))
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
