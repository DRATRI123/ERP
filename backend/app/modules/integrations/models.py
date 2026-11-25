"""Integrations: connections, call logs, outbound messages, payment links, webhook receipts."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
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
from app.db.types import Money, UTCDateTime, str_enum


class IntegrationStatus(enum.StrEnum):
    CONNECTED = "connected"
    PENDING = "pending"  # e.g. waiting for OAuth consent
    ERROR = "error"
    DISCONNECTED = "disconnected"


class Integration(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("business_id", "name"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[IntegrationStatus] = mapped_column(str_enum(IntegrationStatus, 14), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # non-secret
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)  # Fernet token, never returned
    credentials_rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
    # Removed by the user but kept because messages / payments point at it; hidden from lists.
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class IntegrationLog(UUIDPrimaryKey, Base):
    """One external call or inbound webhook. Secrets are never logged."""

    __tablename__ = "integration_logs"
    __table_args__ = (Index("ix_integration_logs_integration_created", "integration_id", "created_at"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # outbound / inbound
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    success: Mapped[bool] = mapped_column(nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(String(1000))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class MessageChannel(enum.StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"


class MessageStatus(enum.StrEnum):
    QUEUED = "queued"
    SENT = "sent"  # accepted by the provider
    DELIVERED = "delivered"  # confirmed by a provider webhook
    READ = "read"
    FAILED = "failed"  # gave up after retries, or the provider reported failure


class OutboundMessage(UUIDPrimaryKey, Timestamps, Base):
    """A message waiting for / sent by the worker. Sending never happens inside the request
    that asked for it, so a slow or failing provider never blocks billing."""

    __tablename__ = "outbound_messages"
    __table_args__ = (Index("ix_outbound_messages_due", "status", "next_attempt_at"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integrations.id", ondelete="RESTRICT"), nullable=False)
    channel: Mapped[MessageChannel] = mapped_column(str_enum(MessageChannel, 10), nullable=False)
    recipient: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str | None] = mapped_column(String(80))
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="RESTRICT"))  # attach this document as PDF
    status: Mapped[MessageStatus] = mapped_column(str_enum(MessageStatus, 10), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
    provider_message_id: Mapped[str | None] = mapped_column(String(200), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"))


class PaymentLinkStatus(enum.StrEnum):
    CREATED = "created"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentLink(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "payment_links"
    __table_args__ = (UniqueConstraint("integration_id", "provider_link_id"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integrations.id", ondelete="RESTRICT"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False, index=True)
    provider_link_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reference: Mapped[str] = mapped_column(String(60), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[PaymentLinkStatus] = mapped_column(str_enum(PaymentLinkStatus, 16), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)


class GatewayPayment(UUIDPrimaryKey, Base):
    """A payment the gateway confirmed, and the ERP receipt it became (exactly once)."""

    __tablename__ = "gateway_payments"
    __table_args__ = (UniqueConstraint("integration_id", "provider_payment_id"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integrations.id", ondelete="RESTRICT"), nullable=False)
    link_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("payment_links.id", ondelete="RESTRICT"))
    provider_payment_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    method: Mapped[str | None] = mapped_column(String(30))
    erp_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("payments.id", ondelete="RESTRICT"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class WebhookReceipt(UUIDPrimaryKey, Base):
    """Replay protection: each provider event id is processed once."""

    __tablename__ = "webhook_receipts"
    __table_args__ = (UniqueConstraint("integration_id", "event_id"),)

    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
