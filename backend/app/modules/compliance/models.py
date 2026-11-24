"""Licence and compliance register."""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import str_enum


class LicenceKind(enum.StrEnum):
    DRUG_RETAIL = "drug_retail"  # Form 20/21
    DRUG_WHOLESALE = "drug_wholesale"  # Form 20B/21B
    AYUSH_MANUFACTURING = "ayush_manufacturing"  # Form 25-D
    FSSAI = "fssai"
    GST = "gst"
    TRADE = "trade"
    SHOP_ESTABLISHMENT = "shop_establishment"
    UDYAM = "udyam"
    IEC = "iec"
    POLLUTION = "pollution"
    FIRE = "fire"
    OTHER = "other"


KIND_LABEL = {
    LicenceKind.DRUG_RETAIL: "Drug licence (retail)", LicenceKind.DRUG_WHOLESALE: "Drug licence (wholesale)",
    LicenceKind.AYUSH_MANUFACTURING: "AYUSH manufacturing licence", LicenceKind.FSSAI: "FSSAI",
    LicenceKind.GST: "GST registration", LicenceKind.TRADE: "Trade licence",
    LicenceKind.SHOP_ESTABLISHMENT: "Shop & establishment", LicenceKind.UDYAM: "Udyam (MSME)",
    LicenceKind.IEC: "Import-export code", LicenceKind.POLLUTION: "Pollution consent",
    LicenceKind.FIRE: "Fire NOC", LicenceKind.OTHER: "Other",
}
# Short label printed before the number on invoices.
PRINT_LABEL = {
    LicenceKind.DRUG_RETAIL: "D.L. No.", LicenceKind.DRUG_WHOLESALE: "D.L. No.",
    LicenceKind.AYUSH_MANUFACTURING: "Mfg. Lic. No.", LicenceKind.FSSAI: "FSSAI Lic. No.",
    LicenceKind.GST: "GSTIN", LicenceKind.UDYAM: "Udyam No.", LicenceKind.IEC: "IEC",
}


class Licence(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "licences"
    __table_args__ = (Index("ix_licences_business_expiry", "business_id", "expiry_date"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("branches.id", ondelete="RESTRICT"))  # None = all
    kind: Mapped[LicenceKind] = mapped_column(str_enum(LicenceKind, 30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    number: Mapped[str] = mapped_column(String(80), nullable=False)
    issued_by: Mapped[str | None] = mapped_column(String(160))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)  # None = does not expire
    print_on_invoice: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("files.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Smallest renewal-alert stage already announced (90, 60, 30, 0).
    alert_stage: Mapped[int | None] = mapped_column(Integer)
