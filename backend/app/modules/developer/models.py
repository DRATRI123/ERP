"""Developer platform: API applications and keys, request logs, idempotency, webhooks."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.db.types import UTCDateTime, str_enum


class ApiClient(UUIDPrimaryKey, Timestamps, Base):
    """An external application (clinic app, website, script) that calls the ERP API."""

    __tablename__ = "api_clients"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    # The ERP user the application acts for; its permissions cap the application's.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    business_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    branch_ids: Mapped[list[str] | None] = mapped_column(JSON)  # None = all branches
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ApiKey(UUIDPrimaryKey, Base):
    """Key format: ``erp_<prefix>_<secret>``. Only the SHA-256 of the whole key is stored;
    the prefix identifies the key in logs and dashboards."""

    __tablename__ = "api_keys"

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class ApiRequestLog(UUIDPrimaryKey, Base):
    __tablename__ = "api_request_logs"
    __table_args__ = (Index("ix_api_request_logs_client_created", "client_id", "created_at"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    request_id: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class IdempotencyRecord(UUIDPrimaryKey, Base):
    """A POST made with an Idempotency-Key, and the response it produced (kept 24 h)."""

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("identity", "key"),)

    identity: Mapped[str] = mapped_column(String(80), nullable=False)  # user or API key
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(300), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(100))
    response_body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)


class EndpointStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"  # by the user, or automatically after repeated failures


class WebhookEndpoint(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "webhook_endpoints"

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # During rotation the previous secret also signs, until this time.
    previous_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    previous_secret_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[EndpointStatus] = mapped_column(str_enum(EndpointStatus, 10), nullable=False)
    disabled_reason: Mapped[str | None] = mapped_column(String(200))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class DeliveryStatus(enum.StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # gave up after all retries


class WebhookDelivery(UUIDPrimaryKey, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "event_id"),
        Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),
    )

    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("outbox_events.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(str_enum(DeliveryStatus, 10), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(String(500))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

