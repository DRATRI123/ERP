"""Automation rules ("when <event> [if <conditions>] [after <delay>] then <action>") and their runs."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.db.types import UTCDateTime, str_enum


class AutomationRule(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "automation_rules"
    __table_args__ = (Index("ix_automation_rules_trigger", "business_id", "trigger", "is_active"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trigger: Mapped[str] = mapped_column(String(60), nullable=False)
    # [{"field": "grand_total", "op": "gte", "value": "50000"}, …] — all must hold.
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    action_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class RunStatus(enum.StrEnum):
    SCHEDULED = "scheduled"  # waiting for its delay
    DONE = "done"
    SKIPPED = "skipped"  # conditions no longer held when it was due (e.g. invoice paid meanwhile)
    FAILED = "failed"


class AutomationRun(UUIDPrimaryKey, Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        UniqueConstraint("rule_id", "event_id"),  # each event runs a rule at most once
        Index("ix_automation_runs_due", "status", "run_at"),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("automation_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("outbox_events.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus, 10), nullable=False)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    detail: Mapped[str | None] = mapped_column(String(500))
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
