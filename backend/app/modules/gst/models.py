"""GST records: RCM self-invoices and job-work challans (ITC-04)."""

from __future__ import annotations

import enum
import uuid
from datetime import date
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import str_enum


class RcmSelfInvoice(UUIDPrimaryKey, Timestamps, Base):
    """Self-invoice (section 31(3)(f)) for a reverse-charge purchase from an unregistered supplier."""

    __tablename__ = "rcm_self_invoices"
    __table_args__ = (UniqueConstraint("document_id"), UniqueConstraint("business_id", "number"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class JobWorkDirection(enum.StrEnum):
    OUT = "out"  # sent to the job worker
    IN = "in"  # received back


class JobWorkNature(enum.StrEnum):
    INPUTS = "inputs"  # must come back within 1 year
    CAPITAL_GOODS = "capital_goods"  # within 3 years


class JobWorkChallan(UUIDPrimaryKey, Timestamps, Base):
    """Goods sent to / received back from a job worker without paying GST (section 143), as a stock
    transfer to the job worker's own godown."""

    __tablename__ = "job_work_challans"
    __table_args__ = (UniqueConstraint("business_id", "number"),
                      Index("ix_job_work_business_date", "business_id", "challan_date"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[JobWorkDirection] = mapped_column(str_enum(JobWorkDirection, 4), nullable=False)
    nature: Mapped[JobWorkNature] = mapped_column(str_enum(JobWorkNature, 14), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)
    challan_date: Mapped[date] = mapped_column(Date, nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    job_warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    stock_document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_documents.id", ondelete="RESTRICT"), nullable=False)
    sent_challan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("job_work_challans.id", ondelete="RESTRICT"))
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    process: Mapped[str | None] = mapped_column(String(120))  # e.g. "powdering", "packing"
    notes: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
