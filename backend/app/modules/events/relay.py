"""Outbox → Redis Stream relay (run by the worker).

Events are written to the database in the same transaction as the change that caused them; the
relay then appends them, in commit order, to the stream ``erp:events`` and marks them published.
If Redis is down nothing is lost: unpublished rows stay in the outbox and go out on the next run.
Delivery is at-least-once (a crash between XADD and the commit can repeat an event), so every
event carries its stable ``id`` for consumers to de-duplicate.

The stream is capped (approximate MAXLEN) — it is a live feed and short replay buffer for
reconnecting clients, not an archive; the database outbox is the record.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.events.models import OutboxEvent


def publish_pending(db: Session, client: redis.Redis, *, limit: int = 500) -> int:
    settings = get_settings()
    rows = list(db.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None))
                           .order_by(OutboxEvent.created_at, OutboxEvent.id).limit(limit)))
    if not rows:
        db.rollback()
        return 0
    pipe = client.pipeline(transaction=False)
    for ev in rows:
        pipe.xadd(settings.event_stream, {
            "id": str(ev.id), "type": ev.event_type,
            "business_id": str(ev.business_id) if ev.business_id else "",
            "created_at": ev.created_at.isoformat(),
            "data": json.dumps(ev.payload, separators=(",", ":"), default=str),
        }, maxlen=settings.event_stream_maxlen, approximate=True)
    try:
        pipe.execute()
    except redis.RedisError as exc:
        db.rollback()
        for ev in rows:  # recorded for operators; retried on the next run
            ev.attempts += 1
            ev.last_error = str(exc)[:500]
        db.commit()
        raise
    now = datetime.now(UTC)
    for ev in rows:
        ev.published_at = now
    db.commit()
    return len(rows)
