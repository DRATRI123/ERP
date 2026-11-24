"""Emit domain events into the transactional outbox."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.service import jsonable
from app.modules.events.models import OutboxEvent


def emit(
    db: Session,
    event_type: str,
    *,
    aggregate_type: str,
    aggregate_id: Any,
    payload: dict[str, Any],
    business_id: uuid.UUID | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload=jsonable(payload),
        business_id=business_id,
    )
    db.add(event)
    return event
