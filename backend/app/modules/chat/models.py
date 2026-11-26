"""One-to-one messages between people of the same business (salespeople, staff and the owner)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey, utcnow
from app.db.types import UTCDateTime


class ChatMessage(UUIDPrimaryKey, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_recipient", "business_id", "recipient_id", "created_at"),
        Index("ix_chat_messages_pair", "business_id", "sender_id", "recipient_id", "created_at"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("files.id", ondelete="SET NULL"))  # photo
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="SET NULL"))
    document_label: Mapped[str | None] = mapped_column(String(120))  # "Order SO/26-27/0002 · ₹493"
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
