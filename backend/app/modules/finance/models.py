"""Budgets, daily cash closing and balance confirmations."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, UTCDateTime, str_enum


class Budget(UUIDPrimaryKey, Timestamps, Base):
    """Budget for one account (optionally one cost centre) for one month."""

    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("business_id", "account_id", "cost_centre_id", "month"),
                      Index("ix_budgets_business_month", "business_id", "month"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("cost_centres.id", ondelete="CASCADE"))
    month: Mapped[date] = mapped_column(Date, nullable=False)  # first day
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)


class CashClosing(UUIDPrimaryKey, Timestamps, Base):
    """End-of-day cash count for a cash account: expected (books) vs counted, difference posted."""

    __tablename__ = "cash_closings"
    __table_args__ = (UniqueConstraint("business_id", "account_id", "closing_date"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    closing_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    counted: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    difference: Mapped[Decimal] = mapped_column(Money(), nullable=False)  # counted - expected
    denominations: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # {"500": 12, "coins": "35"}
    notes: Mapped[str | None] = mapped_column(String(300))
    closed_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class ConfirmationStatus(enum.StrEnum):
    SENT = "sent"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"


class BalanceConfirmation(UUIDPrimaryKey, Timestamps, Base):
    """A ledger balance sent to a customer / supplier to confirm through a link (no login)."""

    __tablename__ = "balance_confirmations"
    __table_args__ = (UniqueConstraint("token"), Index("ix_balance_conf_business_party", "business_id", "party_id"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Money(), nullable=False)  # + they owe us, - we owe them
    statement: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # snapshot shown on the link
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ConfirmationStatus] = mapped_column(str_enum(ConfirmationStatus, 10), nullable=False)
    response_note: Mapped[str | None] = mapped_column(String(500))
    their_balance: Mapped[Decimal | None] = mapped_column(Money())
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
