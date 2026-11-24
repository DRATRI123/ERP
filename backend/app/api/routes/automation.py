"""Automation rules: catalogue, CRUD and run history."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import DB, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.modules.access.service import BusinessAccess
from app.modules.automation import service as automation
from app.modules.automation.models import AutomationRule, AutomationRun, RunStatus

router = APIRouter(prefix="/businesses/{business_id}/automation", tags=["automation"])
Manage = Annotated[BusinessAccess, Depends(require("automation.manage"))]


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(max_length=40)
    op: str = Field(max_length=10)
    value: str | int | float | None = None


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    is_active: bool = True
    trigger: str = Field(max_length=60)
    conditions: list[Condition] = Field(default_factory=list, max_length=10)
    delay_minutes: int = Field(0, ge=0)
    action: str = Field(max_length=30)
    action_config: dict[str, Any] = Field(default_factory=dict)


class RuleUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=120)
    is_active: bool | None = None
    trigger: str | None = Field(None, max_length=60)
    conditions: list[Condition] | None = Field(None, max_length=10)
    delay_minutes: int | None = Field(None, ge=0)
    action: str | None = Field(None, max_length=30)
    action_config: dict[str, Any] | None = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    is_active: bool
    trigger: str
    conditions: list[dict[str, Any]]
    delay_minutes: int
    action: str
    action_config: dict[str, Any]
    last_run_at: datetime | None
    run_count: int
    created_at: datetime
    updated_at: datetime


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rule_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    status: RunStatus
    run_at: datetime
    executed_at: datetime | None
    detail: str | None
    message_id: uuid.UUID | None
    created_at: datetime


@router.get("/catalog")
def get_catalog(access: Manage) -> dict[str, Any]:
    return automation.catalog()


@router.get("/rules", response_model=list[RuleOut])
def list_rules(access: Manage, db: DB) -> list[AutomationRule]:
    return list(db.scalars(select(AutomationRule).where(AutomationRule.business_id == access.business.id)
                           .order_by(AutomationRule.created_at)))


@router.post("/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: RuleIn, access: Manage, db: DB) -> AutomationRule:
    rule = automation.create_rule(db, access, body.model_dump())
    db.commit()
    return rule


@router.get("/rules/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: uuid.UUID, access: Manage, db: DB) -> AutomationRule:
    return automation.get_rule(db, access, rule_id)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: uuid.UUID, body: RuleUpdateIn, access: Manage, db: DB) -> AutomationRule:
    rule = automation.update_rule(db, access, automation.get_rule(db, access, rule_id),
                                  body.model_dump(exclude_unset=True))
    db.commit()
    return rule


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: uuid.UUID, access: Manage, db: DB) -> None:
    automation.delete_rule(db, access, automation.get_rule(db, access, rule_id))
    db.commit()


@router.get("/runs", response_model=Page[RunOut])
def list_runs(access: Manage, db: DB, params: Annotated[PageParams, Depends(page_params)],
              rule_id: uuid.UUID | None = None,
              status_: Annotated[RunStatus | None, Query(alias="status")] = None) -> dict[str, Any]:
    stmt = select(AutomationRun).where(AutomationRun.business_id == access.business.id)
    if rule_id:
        stmt = stmt.where(AutomationRun.rule_id == rule_id)
    if status_:
        stmt = stmt.where(AutomationRun.status == status_)
    items, total = paginate(db, stmt.order_by(AutomationRun.created_at.desc()), params)
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}
