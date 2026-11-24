from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, select

from app.api.deps import DB, OwnerPrincipal, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import AuditLogOut
from app.modules.access.service import BusinessAccess
from app.modules.audit.models import AuditLog

router = APIRouter(tags=["audit"])


def _filtered(
    stmt: Select, action: str | None, entity_type: str | None, entity_id: str | None,
    actor_user_id: uuid.UUID | None, since: datetime | None, until: datetime | None,
) -> Select:
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_user_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at < until)
    return stmt.order_by(AuditLog.created_at.desc(), AuditLog.id)


@router.get("/businesses/{business_id}/audit-logs", response_model=Page[AuditLogOut])
def business_audit_logs(
    access: Annotated[BusinessAccess, Depends(require("audit.view"))],
    db: DB,
    params: Annotated[PageParams, Depends(page_params)],
    action: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    since: datetime | None = Query(None), until: datetime | None = Query(None),
) -> Page[AuditLogOut]:
    stmt = _filtered(select(AuditLog).where(AuditLog.business_id == access.business.id),
                     action, entity_type, entity_id, actor_user_id, since, until)
    rows, total = paginate(db, stmt, params)
    return Page(items=[AuditLogOut.model_validate(r) for r in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.get("/audit-logs", response_model=Page[AuditLogOut])
def all_audit_logs(
    _: OwnerPrincipal,
    db: DB,
    params: Annotated[PageParams, Depends(page_params)],
    business_id: uuid.UUID | None = None,
    action: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    since: datetime | None = Query(None), until: datetime | None = Query(None),
) -> Page[AuditLogOut]:
    """Owner-wide audit trail, including account-level events (no business)."""
    stmt = select(AuditLog)
    if business_id:
        stmt = stmt.where(AuditLog.business_id == business_id)
    stmt = _filtered(stmt, action, entity_type, entity_id, actor_user_id, since, until)
    rows, total = paginate(db, stmt, params)
    return Page(items=[AuditLogOut.model_validate(r) for r in rows], total=total,
                limit=params.limit, offset=params.offset)
