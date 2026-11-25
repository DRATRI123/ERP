"""Cheques (post-dated register) and TDS deposits."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, UTCDateTime, str_enum
from app.modules.billing.models import PaymentDirection


class ChequeStatus(enum.StrEnum):
    PENDING = "pending"  # in hand / issued, not yet cleared — not in the books
    CLEARED = "cleared"  # cleared by the bank: a receipt or payment was recorded
    BOUNCED = "bounced"  # dishonoured (the payment, if any, was cancelled)
    CANCELLED = "cancelled"  # returned / torn up before presenting


class Cheque(UUIDPrimaryKey, Timestamps, Base):
    """A cheque received from a customer or issued to a supplier, usually post-dated.

    It stays out of the books until it clears; clearing records the receipt or payment (and
    settles the chosen bills), a bounce after clearing cancels that payment again.
    """

    __tablename__ = "cheques"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_cheques_business_status_date", "business_id", "status", "cheque_date"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("branches.id", ondelete="RESTRICT"))
    direction: Mapped[PaymentDirection] = mapped_column(str_enum(PaymentDirection, 4), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)
    cheque_no: Mapped[str] = mapped_column(String(20), nullable=False)
    cheque_date: Mapped[date] = mapped_column(Date, nullable=False)  # date written on the cheque
    bank_name: Mapped[str | None] = mapped_column(String(80))  # drawee bank (theirs for received cheques)
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    # Our bank account it is deposited into / drawn on (cash/bank account in the books).
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"))
    payee_name: Mapped[str | None] = mapped_column(String(120))  # issued cheques: name as printed
    allocations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)  # bills to settle
    notes: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[ChequeStatus] = mapped_column(str_enum(ChequeStatus, 10), default=ChequeStatus.PENDING, nullable=False)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("payments.id", ondelete="RESTRICT"))
    cleared_on: Mapped[date | None] = mapped_column(Date)
    bounced_on: Mapped[date | None] = mapped_column(Date)
    bounce_reason: Mapped[str | None] = mapped_column(String(200))
    due_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class TdsDeposit(UUIDPrimaryKey, Timestamps, Base):
    """TDS paid to the government by challan (ITNS 281) for a month's deductions."""

    __tablename__ = "tds_deposits"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),
                      Index("ix_tds_deposits_business_month", "business_id", "month"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)  # first day of the month of deduction
    section: Mapped[str | None] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    deposit_date: Mapped[date] = mapped_column(Date, nullable=False)
    challan_no: Mapped[str | None] = mapped_column(String(20))
    bsr_code: Mapped[str | None] = mapped_column(String(7))
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
