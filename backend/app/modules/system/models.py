"""System-level records: custom domains, settings, backups, security events, 2FA codes."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.db.types import UTCDateTime, str_enum


class DomainStatus(enum.StrEnum):
    PENDING = "pending"  # waiting for the DNS TXT record
    ACTIVE = "active"  # verified: served, certificate allowed
    DISABLED = "disabled"  # by the owner, or after verification kept failing


class CustomDomain(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "custom_domains"

    hostname: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    # Optional: which business the frontend should open on this domain. Grants nothing.
    default_business_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="SET NULL"))
    verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DomainStatus] = mapped_column(str_enum(DomainStatus, 10), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class SystemSetting(Base):
    """Owner-controlled settings (backup schedule…) and worker heartbeats, as JSON by key."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class BackupStatus(enum.StrEnum):
    QUEUED = "queued"  # requested by the owner; the worker picks it up
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BackupRun(UUIDPrimaryKey, Base):
    __tablename__ = "backup_runs"
    __table_args__ = (Index("ix_backup_runs_started", "started_at"),)

    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[BackupStatus] = mapped_column(str_enum(BackupStatus, 10), nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)  # schedule / manual / cli
    filename: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))  # of the encrypted file
    verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    offsite: Mapped[str | None] = mapped_column(String(200))  # e.g. "google_drive:<file id>"
    error: Mapped[str | None] = mapped_column(String(500))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # pruned by retention


class SecurityEvent(UUIDPrimaryKey, Base):
    """Things a user should know about their account (new-device sign-in, 2FA changes)."""

    __tablename__ = "security_events"
    __table_args__ = (Index("ix_security_events_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    emailed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    email_error: Mapped[str | None] = mapped_column(String(300))


class RecoveryCode(UUIDPrimaryKey, Base):
    __tablename__ = "recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
