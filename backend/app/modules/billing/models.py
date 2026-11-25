"""Billing documents, lines, numbering series, payments, HSN tax rates and print templates.

A document is editable while ``draft``. Finalizing assigns its number, freezes the buyer and
seller details (later edits to the customer never change an issued invoice), posts stock and
makes it immutable. A finalized document is never edited or deleted — it is cancelled, which
reverses its stock, and corrections are made with credit/debit notes.
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

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, Quantity, UTCDateTime, str_enum
from app.modules.billing.doctypes import DocType
from app.modules.catalog.models import Rate
from app.modules.gst.tax import TaxCategory
from app.modules.pharmacy.models import Doctor


class DocStatus(enum.StrEnum):
    DRAFT = "draft"
    FINAL = "final"
    CANCELLED = "cancelled"


class TaxMode(enum.StrEnum):
    GST = "gst"  # tax invoice
    BILL_OF_SUPPLY = "bill_of_supply"  # composition dealer / exempt supplies: no tax charged


class PriceTier(enum.StrEnum):
    RETAIL = "retail"
    WHOLESALE = "wholesale"
    MRP = "mrp"
    PTR = "ptr"  # pharma: price to retailer
    PTS = "pts"  # pharma: price to stockist


class PaymentStatus(enum.StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"


class DocumentSeries(UUIDPrimaryKey, Timestamps, Base):
    """Numbering format for a document type, optionally per branch.

    ``prefix``/``suffix`` may contain ``{FY}`` (e.g. 2026-27), ``{FYS}`` (e.g. 2627) and
    ``{BRANCH}`` (branch code). Counters restart every financial year.
    """

    __tablename__ = "document_series"
    __table_args__ = (UniqueConstraint("business_id", "doc_type", "name"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="RESTRICT"))
    doc_type: Mapped[DocType] = mapped_column(str_enum(DocType, 20), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    prefix: Mapped[str] = mapped_column(String(30), nullable=False)
    suffix: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    padding: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Document(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("business_id", "doc_type", "number"),
        UniqueConstraint("business_id", "client_ref"),
        Index("ix_documents_business_type_date", "business_id", "doc_type", "doc_date"),
        Index("ix_documents_party", "party_id", "status"),
        Index("ix_documents_reference", "reference_document_id"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False)
    doc_type: Mapped[DocType] = mapped_column(str_enum(DocType, 20), nullable=False)
    status: Mapped[DocStatus] = mapped_column(
        str_enum(DocStatus, 10), default=DocStatus.DRAFT, nullable=False)
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_series.id", ondelete="RESTRICT"))
    financial_year_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("financial_years.id", ondelete="RESTRICT"))
    number: Mapped[str | None] = mapped_column(String(40))  # assigned on finalize
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    # Idempotency: a client (offline device, external app) may tag a document with its own id.
    client_ref: Mapped[str | None] = mapped_column(String(80))

    party_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"))
    party_name: Mapped[str | None] = mapped_column(String(200))  # walk-in / snapshot
    party_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    seller_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    seller_gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    seller_state: Mapped[str | None] = mapped_column(String(2))
    place_of_supply: Mapped[str | None] = mapped_column(String(2))
    billing_address_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("party_addresses.id", ondelete="SET NULL"))
    shipping_address_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("party_addresses.id", ondelete="SET NULL"))

    tax_mode: Mapped[TaxMode] = mapped_column(
        str_enum(TaxMode, 16), default=TaxMode.GST, nullable=False)
    is_interstate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_utgst: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    price_tier: Mapped[PriceTier] = mapped_column(
        str_enum(PriceTier, 10), default=PriceTier.RETAIL, nullable=False)
    prices_include_tax: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"))
    restock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # credit notes
    reference_document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="RESTRICT"))
    supplier_invoice_no: Mapped[str | None] = mapped_column(String(40))
    supplier_invoice_date: Mapped[date | None] = mapped_column(Date)
    landed_cost: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    salesperson_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    # Pharmacy: prescription details, required when a line is a Schedule H/H1/X/narcotic drug.
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("doctors.id", ondelete="RESTRICT"), index=True)
    patient_name: Mapped[str | None] = mapped_column(String(120))
    patient_address: Mapped[str | None] = mapped_column(String(300))
    rx_number: Mapped[str | None] = mapped_column(String(40))
    doctor: Mapped[Doctor | None] = relationship(lazy="select")

    subtotal: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    taxable_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    cgst_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    sgst_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    igst_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    cess_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    round_off: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    # What the counter-party actually pays (excludes reverse-charge tax).
    amount_payable: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    balance_due: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        str_enum(PaymentStatus, 16), default=PaymentStatus.NOT_APPLICABLE, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text)
    terms: Mapped[str | None] = mapped_column(Text)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # E-invoice (IRN) details, recorded after the IRP has generated them — never invented.
    irn: Mapped[str | None] = mapped_column(String(64))
    irn_ack_no: Mapped[str | None] = mapped_column(String(30))
    irn_ack_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    irn_signed_qr: Mapped[str | None] = mapped_column(Text)
    # E-way bill (goods moved worth over ₹50,000): recorded after generating it on the portal.
    eway_bill_no: Mapped[str | None] = mapped_column(String(12))
    eway_bill_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    eway_valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    vehicle_no: Mapped[str | None] = mapped_column(String(15))
    transporter_id: Mapped[str | None] = mapped_column(String(15))
    transporter_name: Mapped[str | None] = mapped_column(String(100))
    transport_distance_km: Mapped[int | None] = mapped_column()
    overdue_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # invoice.overdue sent

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finalized_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    cancel_reason: Mapped[str | None] = mapped_column(String(300))

    lines: Mapped[list[DocumentLine]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="DocumentLine.line_no")

    @property
    def doctor_name(self) -> str | None:
        return self.doctor.name if self.doctor_id and self.doctor else None


class DocumentLine(UUIDPrimaryKey, Base):
    __tablename__ = "document_lines"
    __table_args__ = (
        UniqueConstraint("document_id", "line_no"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        Index("ix_document_lines_source", "source_line_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    hsn_sac: Mapped[str | None] = mapped_column(String(8))
    unit_code: Mapped[str | None] = mapped_column(String(16))
    uqc: Mapped[str | None] = mapped_column(String(8))
    is_service: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity(), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    mrp: Mapped[Decimal | None] = mapped_column(Money())
    discount_percent: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    price_includes_tax: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tax_category: Mapped[TaxCategory] = mapped_column(
        str_enum(TaxCategory, 12), default=TaxCategory.TAXABLE, nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)
    itc_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    gross_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    taxable_value: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    cgst_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    sgst_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    igst_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    cess_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)

    batch_no: Mapped[str | None] = mapped_column(String(60))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    serials: Mapped[list[str] | None] = mapped_column(JSON)
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_lines.id", ondelete="RESTRICT"))
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class PaymentDirection(enum.StrEnum):
    IN = "in"  # receipt from a customer
    OUT = "out"  # payment to a supplier


class PaymentMode(enum.StrEnum):
    CASH = "cash"
    UPI = "upi"
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    CHEQUE = "cheque"
    OTHER = "other"


class PaymentState(enum.StrEnum):
    POSTED = "posted"
    CANCELLED = "cancelled"


class Payment(UUIDPrimaryKey, Timestamps, Base):
    """Money received or paid, allocated to one or more documents. Phase 4 posts these to the
    cash/bank and party ledgers."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("business_id", "number"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("tds_amount >= 0", name="tds_non_negative"),
        Index("ix_payments_business_date", "business_id", "payment_date"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False)
    financial_year_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("financial_years.id", ondelete="RESTRICT"), nullable=False)
    direction: Mapped[PaymentDirection] = mapped_column(str_enum(PaymentDirection, 4), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    party_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), index=True)
    mode: Mapped[PaymentMode] = mapped_column(str_enum(PaymentMode, 16), nullable=False)
    # Cash/bank account the money moved through; default: Cash for cash, Bank otherwise.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))  # UTR, cheque no., card ref
    # TDS deducted on this payment (by us on a supplier payment, or by the customer on a receipt).
    # ``amount`` is the money that moved; amount + tds_amount is what settles the bills.
    tds_amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), server_default="0", nullable=False)
    tds_section: Mapped[str | None] = mapped_column(String(10))  # e.g. 194C, 194J, 194Q
    notes: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[PaymentState] = mapped_column(
        str_enum(PaymentState, 10), default=PaymentState.POSTED, nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(300))

    allocations: Mapped[list[PaymentAllocation]] = relationship(
        cascade="all, delete-orphan", lazy="selectin")


class PaymentAllocation(UUIDPrimaryKey, Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (
        UniqueConstraint("payment_id", "document_id"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)


class HsnTaxRate(UUIDPrimaryKey, Timestamps, Base):
    """Versioned GST rate by HSN/SAC prefix with an effective-date range.

    When a line is priced, the most specific (longest) prefix effective on the document date
    wins over the product's own rate, so a rate notification is entered once, with its date.
    """

    __tablename__ = "hsn_tax_rates"
    __table_args__ = (
        Index("ix_hsn_tax_rates_lookup", "business_id", "hsn_prefix", "effective_from"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from",
                        name="dates_ordered"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    hsn_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Rate(), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(200))


class InvoiceTemplate(UUIDPrimaryKey, Timestamps, Base):
    """Print layout. Resolution order: branch + type, business + type, branch default,
    business default, built-in."""

    __tablename__ = "invoice_templates"

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    doc_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # [] = all
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
