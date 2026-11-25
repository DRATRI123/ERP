"""Supplier item-name memory (purchase bill import), damage / breakage claims and loyalty points."""

from __future__ import annotations

import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, Quantity, ScaledDecimal, str_enum


class SupplierItemMap(UUIDPrimaryKey, Timestamps, Base):
    """"This supplier calls it X" -> our product. Learnt when a supplier's bill file is imported."""

    __tablename__ = "supplier_item_maps"
    __table_args__ = (UniqueConstraint("business_id", "party_id", "supplier_key"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    supplier_key: Mapped[str] = mapped_column(String(200), nullable=False)  # normalised name or code
    supplier_text: Mapped[str] = mapped_column(String(200), nullable=False)  # as written on their bill
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)


class ClaimStatus(enum.StrEnum):
    OPEN = "open"  # claimed from the supplier, waiting
    SETTLED = "settled"  # supplier gave credit (fully or partly)
    REJECTED = "rejected"  # supplier refused: the loss stays
    WRITTEN_OFF = "written_off"  # no one to claim from


class DamageReason(enum.StrEnum):
    BREAKAGE = "breakage"
    LEAKAGE = "leakage"
    TRANSIT = "transit"  # damaged on arrival
    EXPIRY = "expiry"
    QUALITY = "quality"
    OTHER = "other"


class DamageClaim(UUIDPrimaryKey, Timestamps, Base):
    """Damaged stock written off (a stock adjustment), and the claim for it from the supplier."""

    __tablename__ = "damage_claims"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        Index("ix_damage_claims_business_status", "business_id", "status"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    claim_date: Mapped[date] = mapped_column(Date, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    batch_no: Mapped[str | None] = mapped_column(String(60))
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    reason: Mapped[DamageReason] = mapped_column(str_enum(DamageReason, 10), nullable=False)
    cost_value: Mapped[Decimal] = mapped_column(Money(), nullable=False)  # stock value written off
    party_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), index=True)
    claim_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)  # asked from the supplier
    settled_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    status: Mapped[ClaimStatus] = mapped_column(str_enum(ClaimStatus, 12), nullable=False)
    stock_document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_documents.id", ondelete="RESTRICT"), nullable=False)
    settlement_ref: Mapped[str | None] = mapped_column(String(60))  # supplier's credit note no.
    settled_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(500))
    file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("files.id", ondelete="SET NULL"))  # photo
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class LoyaltyKind(enum.StrEnum):
    EARN = "earn"  # on a sales invoice
    REDEEM = "redeem"  # used as payment
    REVERSE = "reverse"  # invoice or redemption cancelled
    ADJUST = "adjust"  # manual (welcome bonus, correction)


class LoyaltyEntry(UUIDPrimaryKey, Timestamps, Base):
    """Points ledger per customer; the balance is the sum."""

    __tablename__ = "loyalty_entries"
    __table_args__ = (Index("ix_loyalty_entries_party", "business_id", "party_id"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[LoyaltyKind] = mapped_column(str_enum(LoyaltyKind, 8), nullable=False)
    points: Mapped[Decimal] = mapped_column(ScaledDecimal(14, 2), nullable=False)  # signed
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), index=True)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("payments.id", ondelete="RESTRICT"), index=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))


class SchemeClaimStatus(enum.StrEnum):
    OPEN = "open"  # sent to the manufacturer, waiting
    SETTLED = "settled"  # credit note received
    REJECTED = "rejected"


class SchemeClaim(UUIDPrimaryKey, Timestamps, Base):
    """What a manufacturer owes us for the free goods and scheme discounts we gave on their behalf.
    ``line_ids`` are the sales lines claimed, so a line is never claimed twice."""

    __tablename__ = "scheme_claims"
    __table_args__ = (UniqueConstraint("business_id", "number"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    claim_date: Mapped[date] = mapped_column(Date, nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    line_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    free_quantity: Mapped[Decimal] = mapped_column(Quantity(), default=Decimal(0), nullable=False)
    claim_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    settled_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    status: Mapped[SchemeClaimStatus] = mapped_column(str_enum(SchemeClaimStatus, 10), nullable=False)
    settlement_ref: Mapped[str | None] = mapped_column(String(60))
    settled_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class ChallanReturn(UUIDPrimaryKey, Timestamps, Base):
    """Goods sent on sale-or-return (delivery challan) that came back unsold."""

    __tablename__ = "challan_returns"
    __table_args__ = (CheckConstraint("quantity > 0", name="challan_return_quantity_positive"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False, index=True)
    line_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document_lines.id", ondelete="RESTRICT"), nullable=False, index=True)
    return_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    stock_document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_documents.id", ondelete="RESTRICT"), nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
