"""Double-entry books: chart of accounts, journals, cost centres, expenses, fixed assets and
bank statement lines.

Every journal entry balances (total debit = total credit) and is immutable once posted.
Mistakes are corrected by a reversing entry, never by editing or deleting. Customer and
supplier ledgers are sub-ledgers of the receivable/payable control accounts, kept by tagging
journal lines with ``party_id``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.db.types import Money, UTCDateTime, str_enum
from app.modules.catalog.models import Rate


class AccountType(enum.StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


class AccountSubtype(enum.StrEnum):
    """Drives where an account appears in the financial statements."""

    CASH = "cash"
    BANK = "bank"
    RECEIVABLE = "receivable"
    INVENTORY = "inventory"
    TAX_ASSET = "tax_asset"
    CURRENT_ASSET = "current_asset"
    FIXED_ASSET = "fixed_asset"
    ACCUMULATED_DEPRECIATION = "accumulated_depreciation"
    OTHER_ASSET = "other_asset"
    PAYABLE = "payable"
    TAX_LIABILITY = "tax_liability"
    CURRENT_LIABILITY = "current_liability"
    LOAN = "loan"
    OTHER_LIABILITY = "other_liability"
    CAPITAL = "capital"
    RETAINED_EARNINGS = "retained_earnings"
    OPENING_BALANCE = "opening_balance"
    DRAWINGS = "drawings"
    SALES = "sales"
    OTHER_INCOME = "other_income"
    COST_OF_GOODS = "cost_of_goods"
    OPERATING_EXPENSE = "operating_expense"
    DEPRECIATION = "depreciation"
    FINANCE_COST = "finance_cost"
    OTHER_EXPENSE = "other_expense"


SUBTYPE_TYPE: dict[AccountSubtype, AccountType] = {
    **dict.fromkeys([AccountSubtype.CASH, AccountSubtype.BANK, AccountSubtype.RECEIVABLE,
                     AccountSubtype.INVENTORY, AccountSubtype.TAX_ASSET,
                     AccountSubtype.CURRENT_ASSET, AccountSubtype.FIXED_ASSET,
                     AccountSubtype.ACCUMULATED_DEPRECIATION, AccountSubtype.OTHER_ASSET],
                    AccountType.ASSET),
    **dict.fromkeys([AccountSubtype.PAYABLE, AccountSubtype.TAX_LIABILITY,
                     AccountSubtype.CURRENT_LIABILITY, AccountSubtype.LOAN,
                     AccountSubtype.OTHER_LIABILITY], AccountType.LIABILITY),
    **dict.fromkeys([AccountSubtype.CAPITAL, AccountSubtype.RETAINED_EARNINGS,
                     AccountSubtype.OPENING_BALANCE, AccountSubtype.DRAWINGS], AccountType.EQUITY),
    **dict.fromkeys([AccountSubtype.SALES, AccountSubtype.OTHER_INCOME], AccountType.INCOME),
    **dict.fromkeys([AccountSubtype.COST_OF_GOODS, AccountSubtype.OPERATING_EXPENSE,
                     AccountSubtype.DEPRECIATION, AccountSubtype.FINANCE_COST,
                     AccountSubtype.OTHER_EXPENSE], AccountType.EXPENSE),
}


class Account(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("business_id", "code"),
        # Accounts the system posts to automatically (cash, receivables, GST, ...).
        UniqueConstraint("business_id", "system_key"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[AccountType] = mapped_column(str_enum(AccountType, 10), nullable=False)
    subtype: Mapped[AccountSubtype] = mapped_column(str_enum(AccountSubtype, 24), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"))
    system_key: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(String(300))
    # Bank details for bank accounts (account number, IFSC, branch).
    bank_details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CostCentre(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "cost_centres"
    __table_args__ = (UniqueConstraint("business_id", "code"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class JournalSource(enum.StrEnum):
    MANUAL = "manual"
    DOCUMENT = "document"  # sales/purchase documents
    PAYMENT = "payment"
    STOCK = "stock"  # opening stock, adjustments, counts
    EXPENSE = "expense"
    DEPRECIATION = "depreciation"
    ASSET = "asset"  # capitalisation / disposal
    OPENING = "opening"
    CLOSING = "closing"
    PAYROLL = "payroll"


class JournalEntry(UUIDPrimaryKey, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("business_id", "number"),
        # One posting and at most one reversal per business event: re-running is harmless.
        UniqueConstraint("business_id", "source_type", "source_id", "source_event"),
        Index("ix_journal_entries_business_date", "business_id", "entry_date"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="RESTRICT"))
    financial_year_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("financial_years.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_type: Mapped[JournalSource] = mapped_column(str_enum(JournalSource, 12), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_event: Mapped[str] = mapped_column(String(12), default="post", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(60))  # e.g. the invoice number
    narration: Mapped[str | None] = mapped_column(String(500))
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT"), unique=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    posted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    lines: Mapped[list[JournalLine]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="JournalLine.line_no")


class JournalLine(UUIDPrimaryKey, Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="non_negative"),
        CheckConstraint("(debit = 0) <> (credit = 0)", name="one_side"),
        Index("ix_journal_lines_account", "account_id", "entry_date"),
        Index("ix_journal_lines_party", "party_id", "account_id"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)  # copied for fast ledgers
    debit: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    credit: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    party_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"))
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cost_centres.id", ondelete="RESTRICT"))
    description: Mapped[str | None] = mapped_column(String(300))


class ExpenseStatus(enum.StrEnum):
    POSTED = "posted"
    CANCELLED = "cancelled"


class Expense(UUIDPrimaryKey, Timestamps, Base):
    """Expense voucher: rent, electricity, salaries... optionally with GST input credit."""

    __tablename__ = "expenses"
    __table_args__ = (UniqueConstraint("business_id", "number"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    # Paid immediately from cash/bank, or owed to a supplier (paid_from empty).
    paid_from_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"))
    party_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"))
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cost_centres.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(60))  # supplier bill number
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)  # taxable value
    gst_rate: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)
    itc_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_interstate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cgst: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    sgst: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    igst: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    total: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    status: Mapped[ExpenseStatus] = mapped_column(
        str_enum(ExpenseStatus, 10), default=ExpenseStatus.POSTED, nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(300))


class DepreciationMethod(enum.StrEnum):
    STRAIGHT_LINE = "straight_line"  # SLM: (cost − salvage) / life
    WRITTEN_DOWN = "written_down"  # WDV: rate × opening book value


class FixedAsset(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "fixed_assets"
    __table_args__ = (UniqueConstraint("business_id", "code"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    acquired_on: Mapped[date] = mapped_column(Date, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    salvage_value: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    method: Mapped[DepreciationMethod] = mapped_column(str_enum(DepreciationMethod, 16), nullable=False)
    useful_life_months: Mapped[int | None] = mapped_column(Integer)  # straight line
    annual_rate: Mapped[Decimal | None] = mapped_column(Rate())  # written down value, %
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Money(), default=Decimal(0), nullable=False)
    depreciated_to: Mapped[date | None] = mapped_column(Date)
    disposed_on: Mapped[date | None] = mapped_column(Date)
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cost_centres.id", ondelete="RESTRICT"))
    notes: Mapped[str | None] = mapped_column(Text)


class DepreciationEntry(UUIDPrimaryKey, Base):
    __tablename__ = "depreciation_entries"
    __table_args__ = (UniqueConstraint("asset_id", "period_end"),)

    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("fixed_assets.id", ondelete="RESTRICT"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False)


class BankStatementLine(UUIDPrimaryKey, Base):
    __tablename__ = "bank_statement_lines"
    __table_args__ = (
        Index("ix_bank_statement_lines_account_date", "account_id", "txn_date"),
        CheckConstraint("amount <> 0", name="amount_non_zero"),
        UniqueConstraint("account_id", "fingerprint"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)  # + deposit, − withdrawal
    # Bank's de-duplication fingerprint: the same statement imported twice adds nothing.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    matched_line_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_lines.id", ondelete="RESTRICT"), unique=True)
    matched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
