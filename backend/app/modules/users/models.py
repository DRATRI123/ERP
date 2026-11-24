"""User accounts. A single account may be a member of several businesses."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import UTCDateTime, str_enum


class UserStatus(enum.StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (
        # At most one owner account: is_owner is either NULL or TRUE, and unique. Both SQLite
        # and PostgreSQL allow many NULLs in a unique column, so exactly one TRUE is possible.
        UniqueConstraint("is_owner", name="uq_users_single_owner"),
        CheckConstraint("is_owner IS NULL OR is_owner", name="owner_flag_true_or_null"),
    )

    # Stored lower-cased; uniqueness is therefore case-insensitive on every database.
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # TRUE only for the owner, NULL for everyone else (see table constraints).
    is_owner: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        str_enum(UserStatus, 16),
        default=UserStatus.INVITED,
        nullable=False,
    )
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def owner(self) -> bool:
        return bool(self.is_owner)
