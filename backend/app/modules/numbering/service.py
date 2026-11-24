"""Allocate the next number of a series inside the caller's transaction.

The counter row is locked (``SELECT … FOR UPDATE`` on PostgreSQL; SQLite write transactions are
already serialized by ``BEGIN IMMEDIATE``), so concurrent documents never share a number and a
rolled-back document never consumes one — numbering stays gap-free.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.numbering.models import NumberSeries


def _locked(db: Session, business_id: uuid.UUID, key: str) -> NumberSeries | None:
    return db.scalar(
        select(NumberSeries)
        .where(NumberSeries.business_id == business_id, NumberSeries.key == key)
        .with_for_update()
        # Re-read the locked row: a cached copy in this session may predate other commits,
        # and handing out a stale next_number would duplicate document numbers.
        .execution_options(populate_existing=True)
    )


def next_number(
    db: Session, business_id: uuid.UUID, key: str, *, prefix: str, padding: int = 4
) -> str:
    series = _locked(db, business_id, key)
    if series is None:
        try:
            with db.begin_nested():
                db.add(NumberSeries(business_id=business_id, key=key, prefix=prefix,
                                    padding=padding, next_number=1))
        except IntegrityError:
            pass  # created concurrently by another transaction
        series = _locked(db, business_id, key)
        assert series is not None
    n = series.next_number
    series.next_number = n + 1
    db.flush()
    return f"{series.prefix}{n:0{series.padding}d}"
