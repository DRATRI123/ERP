"""Gap-free document numbering, one counter per business and series key."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class NumberSeries(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "number_series"
    __table_args__ = (UniqueConstraint("business_id", "key"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    # e.g. "stock.adjustment/2026-27" or, from Phase 3, "invoice.gst/HO/2026-27"
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(40), nullable=False)
    padding: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    next_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
