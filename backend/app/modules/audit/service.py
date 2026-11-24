"""Write audit entries inside the caller's transaction (so they commit or roll back with it)."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.context import client_ip_var, request_id_var
from app.modules.audit.models import AuditLog

_REDACTED_FIELDS = {"password", "password_hash", "token", "token_hash", "refresh_token_hash"}


def jsonable(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in (sorted(value) if isinstance(value, (set, frozenset)) else value)]
    return value


def snapshot(obj: Any, fields: list[str]) -> dict[str, Any]:
    return {f: jsonable(getattr(obj, f)) for f in fields if f not in _REDACTED_FIELDS}


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """``{field: {"from": old, "to": new}}`` for every changed field."""
    return {
        k: {"from": before.get(k), "to": after.get(k)}
        for k in sorted(set(before) | set(after))
        if before.get(k) != after.get(k)
    }


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    business_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        business_id=business_id,
        actor_user_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        changes=jsonable(changes) if changes else None,
        ip_address=client_ip_var.get(),
        request_id=request_id_var.get(),
    )
    db.add(entry)
    return entry
