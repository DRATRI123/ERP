"""Scheduled report e-mails ("every morning: yesterday's sales, cash, dues, stock")."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import UTCDateTime, str_enum


class Frequency(enum.StrEnum):
    DAILY = "daily"  # covers yesterday
    WEEKLY = "weekly"  # covers the last 7 days
    MONTHLY = "monthly"  # covers last month


class ScheduledReport(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "scheduled_reports"
    __table_args__ = (Index("ix_scheduled_reports_due", "is_active", "next_run_at"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    frequency: Mapped[Frequency] = mapped_column(str_enum(Frequency, 10), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # weekly: 0 = Monday
    day_of_month: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # monthly: 1–28
    hour: Mapped[int] = mapped_column(Integer, default=8, nullable=False)  # business time zone
    recipients: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    sections: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    integration_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("integrations.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
