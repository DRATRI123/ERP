"""Field sales: beats (routes) of customers per salesman, visits, and monthly targets."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, ScaledDecimal, UTCDateTime, str_enum


class Beat(UUIDPrimaryKey, Timestamps, Base):
    """A route of customers a salesman covers, usually on a fixed weekday."""

    __tablename__ = "beats"
    __table_args__ = (UniqueConstraint("business_id", "name"),
                      CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="weekday_range"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    weekday: Mapped[int | None] = mapped_column(Integer)  # 0 = Monday
    salesman_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    party_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # visiting order
    notes: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class VisitOutcome(enum.StrEnum):
    ORDER = "order"
    PAYMENT = "payment"
    ORDER_AND_PAYMENT = "order_payment"
    NO_ORDER = "no_order"
    CLOSED = "closed"  # shop closed / owner not there
    OTHER = "other"


class Visit(UUIDPrimaryKey, Base):
    __tablename__ = "visits"
    __table_args__ = (Index("ix_visits_business_date", "business_id", "visit_date"),
                      Index("ix_visits_salesman_date", "salesman_id", "visit_date"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    salesman_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)
    beat_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("beats.id", ondelete="SET NULL"))
    visit_date: Mapped[date] = mapped_column(Date, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    outcome: Mapped[VisitOutcome] = mapped_column(str_enum(VisitOutcome, 14), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    latitude: Mapped[Decimal | None] = mapped_column(ScaledDecimal(10, 6))
    longitude: Mapped[Decimal | None] = mapped_column(ScaledDecimal(10, 6))
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="SET NULL"))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("payments.id", ondelete="SET NULL"))
    next_visit: Mapped[date | None] = mapped_column(Date)


class SalesTarget(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "sales_targets"
    __table_args__ = (UniqueConstraint("business_id", "salesman_id", "month"),
                      CheckConstraint("amount >= 0", name="amount_non_negative"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    salesman_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)  # first of the month
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)  # sales (taxable value) target
    collection_amount: Mapped[Decimal | None] = mapped_column(Money())
