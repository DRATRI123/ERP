"""Scheduled report e-mails."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from app.api.deps import DB, require
from app.modules.access.service import BusinessAccess
from app.modules.digest import service
from app.modules.digest.models import Frequency, ScheduledReport

router = APIRouter(prefix="/businesses/{business_id}/scheduled-reports", tags=["reports"])
Manage = Annotated[BusinessAccess, Depends(require("reports.view", "automation.manage"))]


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    frequency: Frequency
    weekday: int = Field(0, ge=0, le=6)
    day_of_month: int = Field(1, ge=1, le=28)
    hour: int = Field(8, ge=0, le=23)
    recipients: list[str] = Field(min_length=1, max_length=10)
    sections: list[str] = Field(min_length=1, max_length=len(service.SECTIONS))
    integration_id: uuid.UUID | None = None
    is_active: bool = True


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    frequency: Frequency
    weekday: int
    day_of_month: int
    hour: int
    recipients: list[str]
    sections: list[str]
    integration_id: uuid.UUID | None
    is_active: bool
    next_run_at: datetime | None
    last_sent_at: datetime | None
    last_error: str | None


@router.get("/sections")
def sections(access: Manage) -> list[dict[str, str]]:
    return [{"key": k, "label": v} for k, v in service.SECTIONS.items()]


@router.get("", response_model=list[ReportOut])
def list_reports(access: Manage, db: DB) -> list[ScheduledReport]:
    return list(db.scalars(select(ScheduledReport).where(ScheduledReport.business_id == access.business.id)
                           .order_by(ScheduledReport.created_at)))


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(body: ReportIn, access: Manage, db: DB) -> ScheduledReport:
    rep = service.save(db, access, body.model_dump())
    db.commit()
    return rep


@router.put("/{report_id}", response_model=ReportOut)
def update_report(report_id: uuid.UUID, body: ReportIn, access: Manage, db: DB) -> ScheduledReport:
    rep = service.save(db, access, body.model_dump(), service.get(db, access, report_id))
    db.commit()
    return rep


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report_id: uuid.UUID, access: Manage, db: DB) -> None:
    db.delete(service.get(db, access, report_id))
    db.commit()


@router.get("/{report_id}/preview", response_class=HTMLResponse)
def preview(report_id: uuid.UUID, access: Manage, db: DB) -> HTMLResponse:
    """The e-mail as it would be sent now (shown sandboxed by the web app)."""
    html = service.preview(db, access, service.get(db, access, report_id))
    return HTMLResponse(html, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


@router.post("/{report_id}/send-now")
def send_now(report_id: uuid.UUID, access: Manage, db: DB) -> dict[str, Any]:
    ids = service.send(db, access, service.get(db, access, report_id), datetime.now(UTC))
    db.commit()
    return {"queued": len(ids)}
