"""Sales follow-through: leads and follow-ups, warranty / AMC and service calls, dispatch with
proof of delivery, and approval of discounts above the allowed limit."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, ScaledDecimal, UTCDateTime, str_enum
from app.modules.catalog.models import Rate

# ---------------------------------------------------------------------------- leads


class LeadStage(enum.StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"  # needs and budget known
    PROPOSAL = "proposal"  # quotation sent
    WON = "won"
    LOST = "lost"


class ActivityKind(enum.StrEnum):
    CALL = "call"
    VISIT = "visit"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    MEETING = "meeting"
    NOTE = "note"


class Lead(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_business_stage", "business_id", "stage"),
                      Index("ix_leads_business_follow_up", "business_id", "next_follow_up"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # person
    company: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    city: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str | None] = mapped_column(String(60))  # walk-in, IndiaMART, referral, WhatsApp…
    requirement: Mapped[str | None] = mapped_column(String(500))
    stage: Mapped[LeadStage] = mapped_column(str_enum(LeadStage, 12), default=LeadStage.NEW, nullable=False)
    expected_value: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    next_follow_up: Mapped[date | None] = mapped_column(Date)
    party_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="SET NULL"))  # once won
    quotation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="SET NULL"))
    lost_reason: Mapped[str | None] = mapped_column(String(200))
    closed_on: Mapped[date | None] = mapped_column(Date)
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class LeadActivity(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "lead_activities"

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[ActivityKind] = mapped_column(str_enum(ActivityKind, 10), nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
    happened_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


# ---------------------------------------------------------------------------- warranty / AMC


class ContractKind(enum.StrEnum):
    WARRANTY = "warranty"  # free repairs for a period after sale
    AMC = "amc"  # paid annual maintenance contract


class ServiceContract(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "service_contracts"
    __table_args__ = (Index("ix_service_contracts_business_end", "business_id", "end_date"),
                      Index("ix_service_contracts_serial", "business_id", "serial_no"),
                      CheckConstraint("end_date >= start_date", name="contract_dates"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[ContractKind] = mapped_column(str_enum(ContractKind, 10), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    party_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(200))  # walk-in buyer
    customer_phone: Mapped[str | None] = mapped_column(String(20))
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    serial_no: Mapped[str | None] = mapped_column(String(80))
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="SET NULL"))  # sale / AMC bill
    line_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    visits_included: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # AMC preventive visits; 0 = on call
    amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    renewed_from_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("service_contracts.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(String(500))


class TicketStatus(enum.StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_PARTS = "waiting_parts"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ServiceTicket(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "service_tickets"
    __table_args__ = (UniqueConstraint("business_id", "number"), Index("ix_service_tickets_business_status", "business_id", "status"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    opened_on: Mapped[date] = mapped_column(Date, nullable=False)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("service_contracts.id", ondelete="SET NULL"))
    party_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"))
    customer_name: Mapped[str | None] = mapped_column(String(200))
    customer_phone: Mapped[str | None] = mapped_column(String(20))
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="RESTRICT"))
    description: Mapped[str | None] = mapped_column(String(300))
    serial_no: Mapped[str | None] = mapped_column(String(80))
    complaint: Mapped[str] = mapped_column(String(1000), nullable=False)
    covered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # under warranty / AMC on the day it was opened
    status: Mapped[TicketStatus] = mapped_column(str_enum(TicketStatus, 14), default=TicketStatus.OPEN, nullable=False)
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[str | None] = mapped_column(String(1000))
    resolved_on: Mapped[date | None] = mapped_column(Date)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="SET NULL"))  # chargeable repair
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


# ---------------------------------------------------------------------------- dispatch


class DispatchStatus(enum.StrEnum):
    PENDING = "pending"  # to be packed
    PACKED = "packed"
    OUT = "out"  # left with the vehicle
    DELIVERED = "delivered"
    FAILED = "failed"  # not delivered (shop closed, refused…)


class Dispatch(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "dispatches"
    __table_args__ = (UniqueConstraint("document_id", name="uq_dispatches_document"),
                      Index("ix_dispatches_business_status", "business_id", "status"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[DispatchStatus] = mapped_column(str_enum(DispatchStatus, 10), default=DispatchStatus.PENDING, nullable=False)
    packages: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    driver_name: Mapped[str | None] = mapped_column(String(100))
    driver_phone: Mapped[str | None] = mapped_column(String(20))
    transporter: Mapped[str | None] = mapped_column(String(100))
    lr_no: Mapped[str | None] = mapped_column(String(40))  # lorry receipt / docket no.
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    receiver_name: Mapped[str | None] = mapped_column(String(100))
    pod_file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("files.id", ondelete="SET NULL"))
    latitude: Mapped[Decimal | None] = mapped_column(ScaledDecimal(10, 6))
    longitude: Mapped[Decimal | None] = mapped_column(ScaledDecimal(10, 6))
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


# ---------------------------------------------------------------------------- discount approval


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DiscountApproval(UUIDPrimaryKey, Timestamps, Base):
    """A bill whose discount is above what the biller may give. Approved in the app by a manager,
    or with the one-time code the manager reads out (derived from the server secret, never stored).
    Approval covers up to ``discount_percent``."""

    __tablename__ = "discount_approvals"
    __table_args__ = (Index("ix_discount_approvals_document", "document_id"),
                      Index("ix_discount_approvals_business_status", "business_id", "status"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Rate(), nullable=False)  # highest line discount asked
    discount_amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    limit_percent: Mapped[Decimal] = mapped_column(Rate(), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[ApprovalStatus] = mapped_column(str_enum(ApprovalStatus, 10), default=ApprovalStatus.PENDING, nullable=False)
    code_expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    note: Mapped[str | None] = mapped_column(String(300))
