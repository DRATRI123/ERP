"""Staff expense claims (with the bill photo) and their approval."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, UTCDateTime, str_enum


class ClaimStatus(enum.StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"  # to be paid
    PAID = "paid"  # recorded as an expense
    REJECTED = "rejected"


class ExpenseClaim(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "expense_claims"
    __table_args__ = (Index("ix_expense_claims_business_status", "business_id", "status"),
                      CheckConstraint("amount > 0", name="claim_amount_positive"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False)
    claimant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("files.id", ondelete="SET NULL"))
    status: Mapped[ClaimStatus] = mapped_column(str_enum(ClaimStatus, 10), default=ClaimStatus.SUBMITTED, nullable=False)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    expense_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("expenses.id", ondelete="RESTRICT"))
