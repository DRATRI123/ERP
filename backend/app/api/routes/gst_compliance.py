"""GSTR-2B reconciliation, GSTR-9 / CMP-08 / GSTR-4, RCM self-invoices, job work (ITC-04), 194Q and 26Q."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import DB, require
from app.core.clock import local_today
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.gst import compliance as svc
from app.modules.gst.models import JobWorkChallan, JobWorkDirection, JobWorkNature

router = APIRouter(prefix="/businesses/{business_id}", tags=["gst"])
GstView = Annotated[BusinessAccess, Depends(require("gst.view"))]
GstFile = Annotated[BusinessAccess, Depends(require("gst.view", "gst.file"))]
StockView = Annotated[BusinessAccess, Depends(require("stock.view"))]
StockEdit = Annotated[BusinessAccess, Depends(require("stock.view", "stock.edit"))]
PurchView = Annotated[BusinessAccess, Depends(require("purchases.view"))]
PayView = Annotated[BusinessAccess, Depends(require("payments.view"))]


@router.post("/gst/2b-reconcile")
async def reconcile_2b(access: GstView, db: DB, date_from: Annotated[date, Form()], date_to: Annotated[date, Form()],
                       file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload the GSTR-2B JSON from the GST portal (Returns → GSTR-2B → Download JSON)."""
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise ValidationFailed("Files up to 20 MB")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailed("This is not a JSON file. Download the GSTR-2B as JSON from the portal.") from exc
    if not isinstance(data, dict):
        raise ValidationFailed("Unexpected GSTR-2B file layout")
    return svc.reconcile_2b(db, access, data=data, date_from=date_from, date_to=date_to)


@router.get("/gst/gstr9")
def gstr9(access: GstView, db: DB, fy_start: date | None = None) -> dict[str, Any]:
    return svc.gstr9(db, access, fy_start=fy_start or local_today())


@router.get("/gst/cmp08")
def cmp08(access: GstView, db: DB, fy_start: date | None = None,
          rate: Decimal = Query(Decimal(1), ge=0, le=6, description="Composition rate %")) -> dict[str, Any]:
    return svc.cmp08(db, access, fy_start=fy_start or local_today(), rate=rate)


@router.get("/gst/rcm-self-invoices")
def rcm_pending(access: GstView, db: DB) -> list[dict[str, Any]]:
    return svc.rcm_pending(db, access)


class SelfInvoiceIn(BaseModel):
    document_id: uuid.UUID
    invoice_date: date = Field(default_factory=local_today)


@router.post("/gst/rcm-self-invoices", status_code=status.HTTP_201_CREATED)
def create_self_invoice(body: SelfInvoiceIn, access: GstFile, db: DB) -> dict[str, Any]:
    si = svc.create_self_invoice(db, access, body.document_id, body.invoice_date)
    db.commit()
    return {"id": si.id, "number": si.number, "date": si.invoice_date}


@router.get("/gst/rcm-self-invoices/{si_id}/print", response_class=HTMLResponse)
def print_self_invoice(si_id: uuid.UUID, access: GstView, db: DB) -> HTMLResponse:
    return HTMLResponse(svc.self_invoice_html(db, access, si_id))


class JobLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]
    value: Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)] | None = None
    batch_no: Annotated[str, StringConstraints(max_length=60)] | None = None


class JobChallanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: JobWorkDirection
    party_id: uuid.UUID
    warehouse_id: uuid.UUID
    challan_date: date = Field(default_factory=local_today)
    nature: JobWorkNature | None = None
    sent_challan_id: uuid.UUID | None = None
    process: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    notes: Annotated[str, StringConstraints(max_length=300)] | None = None
    lines: list[JobLineIn] = Field(min_length=1, max_length=100)


def _job_out(c: JobWorkChallan) -> dict[str, Any]:
    return {k: getattr(c, k) for k in ("id", "number", "direction", "nature", "party_id", "challan_date", "warehouse_id",
                                       "sent_challan_id", "lines", "process", "notes", "stock_document_id")}


@router.get("/job-work")
def list_job_work(access: StockView, db: DB) -> list[dict[str, Any]]:
    return [_job_out(c) for c in svc.job_challans(db, access)]


@router.post("/job-work", status_code=status.HTTP_201_CREATED)
def create_job_work(body: JobChallanIn, access: StockEdit, db: DB) -> dict[str, Any]:
    c = svc.create_job_challan(db, access, body.model_dump())
    db.commit()
    return _job_out(c)


@router.get("/gst/itc04")
def itc04(access: StockView, db: DB, date_from: date, date_to: date) -> dict[str, Any]:
    return svc.itc04(db, access, date_from=date_from, date_to=date_to, today=local_today())


@router.get("/tds/194q")
def tds_194q(access: PurchView, db: DB, fy_start: date | None = None) -> dict[str, Any]:
    return svc.tds_194q(db, access, fy_start=fy_start or local_today())


@router.get("/tds/26q.csv")
def form_26q(access: PayView, db: DB, date_from: date, date_to: date) -> Response:
    buf = io.StringIO()
    csv.writer(buf).writerows(svc.form_26q_rows(db, access, date_from=date_from, date_to=date_to))
    return Response("﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="26Q-{date_from}-{date_to}.csv"'})
