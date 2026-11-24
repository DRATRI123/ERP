"""Authentication state: sessions (refresh-token families), one-time tokens and login history."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey, utcnow
from app.db.types import UTCDateTime, str_enum


class UserSession(UUIDPrimaryKey, Base):
    """One signed-in device. Holds the hash of its *current* refresh token.

    Refresh tokens rotate on every use. The previous hash is kept so that if a stolen,
    already-rotated token is replayed, the whole session is revoked (reuse detection).
    """

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_revoked", "user_id", "revoked_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    previous_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_reason: Mapped[str | None] = mapped_column(String(50))


class TokenPurpose(enum.StrEnum):
    INVITE = "invite"
    PASSWORD_RESET = "password_reset"


class OneTimeToken(UUIDPrimaryKey, Base):
    """Invitation and password-reset tokens. Only the SHA-256 hash is stored."""

    __tablename__ = "one_time_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[TokenPurpose] = mapped_column(
        str_enum(TokenPurpose, 20), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class LoginEvent(UUIDPrimaryKey, Base):
    __tablename__ = "login_events"
    __table_args__ = (Index("ix_login_events_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    event: Mapped[str] = mapped_column(String(30), nullable=False)  # login / logout / refresh_reuse
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(50))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
