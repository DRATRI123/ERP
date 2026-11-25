"""Pharmacy masters: doctors, and the drug schedules that decide what a sale must record."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class DrugSchedule(enum.StrEnum):
    """Drugs and Cosmetics Rules schedule printed on the pack."""

    NONE = "none"
    G = "g"  # "Caution: to be taken under medical supervision"
    H = "h"  # prescription only
    H1 = "h1"  # prescription only + a separate register kept for 3 years
    X = "x"  # prescription in duplicate, register, stock kept under lock
    NARCOTIC = "narcotic"  # NDPS Act


SCHEDULE_LABEL = {
    DrugSchedule.NONE: "None (OTC)", DrugSchedule.G: "Schedule G", DrugSchedule.H: "Schedule H",
    DrugSchedule.H1: "Schedule H1", DrugSchedule.X: "Schedule X", DrugSchedule.NARCOTIC: "Narcotic (NDPS)",
}
# A sale of these needs the prescribing doctor and the patient's name on the bill.
PRESCRIPTION_REQUIRED = (DrugSchedule.H, DrugSchedule.H1, DrugSchedule.X, DrugSchedule.NARCOTIC)
# These also go in the schedule register (and need the prescription number).
REGISTER_SCHEDULES = (DrugSchedule.H1, DrugSchedule.X, DrugSchedule.NARCOTIC)


class Doctor(UUIDPrimaryKey, Timestamps, Base):
    """A prescribing doctor: printed on bills of scheduled drugs and used for doctor-wise sales."""

    __tablename__ = "doctors"
    __table_args__ = (Index("ix_doctors_business_name", "business_id", "name"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    registration_no: Mapped[str | None] = mapped_column(String(40))  # medical council reg. no.
    qualification: Mapped[str | None] = mapped_column(String(80))
    clinic: Mapped[str | None] = mapped_column(String(160))
    address: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(15))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
