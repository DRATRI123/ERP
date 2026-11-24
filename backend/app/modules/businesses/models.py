"""Businesses, branches and financial years.

Every business-owned record in the ERP carries ``business_id``; a business is the isolation
boundary for books, inventory, GST registration, numbering and settings.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import UTCDateTime, str_enum


class BusinessStatus(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class GstRegistrationType(enum.StrEnum):
    REGULAR = "regular"
    COMPOSITION = "composition"
    UNREGISTERED = "unregistered"
    SEZ = "sez"


class AddressMixin:
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state_code: Mapped[str | None] = mapped_column(String(2))  # GST state code, e.g. "27"
    pincode: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(String(2), default="IN", nullable=False)


class Business(UUIDPrimaryKey, Timestamps, AddressMixin, Base):
    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint("fy_start_month BETWEEN 1 AND 12", name="fy_start_month_range"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    pan: Mapped[str | None] = mapped_column(String(10))
    gst_registration_type: Mapped[GstRegistrationType] = mapped_column(
        str_enum(GstRegistrationType, 16),
        default=GstRegistrationType.REGULAR,
        nullable=False,
    )
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    base_currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    fy_start_month: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[BusinessStatus] = mapped_column(
        str_enum(BusinessStatus, 16),
        default=BusinessStatus.ACTIVE,
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class BranchKind(enum.StrEnum):
    HEAD_OFFICE = "head_office"
    BRANCH = "branch"


class Branch(UUIDPrimaryKey, Timestamps, AddressMixin, Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("business_id", "code"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[BranchKind] = mapped_column(
        str_enum(BranchKind, 16),
        default=BranchKind.BRANCH,
        nullable=False,
    )
    # A branch in another state needs its own GST registration.
    gstin: Mapped[str | None] = mapped_column(String(15))
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FinancialYear(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "financial_years"
    __table_args__ = (
        UniqueConstraint("business_id", "code"),
        CheckConstraint("end_date > start_date", name="dates_ordered"),
        Index("ix_financial_years_business_start", "business_id", "start_date"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(9), nullable=False)  # e.g. "2026-27"
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
