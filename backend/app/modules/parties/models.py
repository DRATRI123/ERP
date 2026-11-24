"""Customers and suppliers ("parties"). One party may be both a customer and a supplier."""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, UTCDateTime, str_enum


class PartyRole(enum.StrEnum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class PartyGstType(enum.StrEnum):
    REGULAR = "regular"
    COMPOSITION = "composition"
    UNREGISTERED = "unregistered"
    CONSUMER = "consumer"
    OVERSEAS = "overseas"
    SEZ = "sez"


class PartyGroup(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "party_groups"
    __table_args__ = (UniqueConstraint("business_id", "role", "name"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[PartyRole] = mapped_column(str_enum(PartyRole, 10), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class Party(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "parties"
    __table_args__ = (
        UniqueConstraint("business_id", "code"),
        Index("ix_parties_business_name", "business_id", "name"),
        Index("ix_parties_business_gstin", "business_id", "gstin"),
        CheckConstraint("is_customer OR is_supplier", name="has_role"),
        CheckConstraint("credit_limit IS NULL OR credit_limit >= 0", name="credit_limit_positive"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str | None] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    gst_type: Mapped[PartyGstType] = mapped_column(
        str_enum(PartyGstType, 16), default=PartyGstType.UNREGISTERED, nullable=False
    )
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    # Place of supply for GST: derived from the GSTIN when there is one.
    state_code: Mapped[str | None] = mapped_column(String(2))

    contact_person: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))

    customer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("party_groups.id", ondelete="RESTRICT")
    )
    supplier_group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("party_groups.id", ondelete="RESTRICT")
    )
    credit_limit: Mapped[Decimal | None] = mapped_column(Money())
    # Sales on credit blocked until released (payment in full still allowed).
    credit_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Customer-type prices (hospital, doctor, wholesale…); None = normal prices.
    price_list_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("price_lists.id", ondelete="SET NULL"))
    credit_days: Mapped[int | None] = mapped_column(Integer)

    notes: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    addresses: Mapped[list[PartyAddress]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="PartyAddress.created_at"
    )


class AddressKind(enum.StrEnum):
    BILLING = "billing"
    SHIPPING = "shipping"


class PartyAddress(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "party_addresses"

    party_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[AddressKind] = mapped_column(str_enum(AddressKind, 10), nullable=False)
    label: Mapped[str | None] = mapped_column(String(60))
    address_line1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state_code: Mapped[str | None] = mapped_column(String(2))
    pincode: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(String(2), default="IN", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
