"""GST registers and return preparation. Figures are prepared for filing; nothing is filed."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, require
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.gst import reports

router = APIRouter(prefix="/businesses/{business_id}/gst", tags=["gst"])
GstView = Annotated[BusinessAccess, Depends(require("gst.view"))]
GSTIN_Q = Query(None, pattern=r"^[0-9A-Z]{15}$", description="Limit to one of your GSTINs")


def _period(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise ValidationFailed("date_to must be on or after date_from")
    if (date_to - date_from).days > 400:
        raise ValidationFailed("Choose a period of at most one year")


@router.get("/sales-register")
def sales_register(access: GstView, db: DB, date_from: date, date_to: date, gstin: str | None = GSTIN_Q) -> dict:
    _period(date_from, date_to)
    return reports.register(db, access.business.id, "sales", date_from, date_to, gstin)


@router.get("/purchase-register")
def purchase_register(access: GstView, db: DB, date_from: date, date_to: date,
                      gstin: str | None = GSTIN_Q) -> dict:
    _period(date_from, date_to)
    return reports.register(db, access.business.id, "purchase", date_from, date_to, gstin)


@router.get("/hsn-summary")
def hsn_summary(access: GstView, db: DB, date_from: date, date_to: date, gstin: str | None = GSTIN_Q) -> dict:
    _period(date_from, date_to)
    docs = reports.documents(db, access.business.id, reports.SALES_TYPES, date_from, date_to, gstin)
    return reports.hsn_summary(docs)


@router.get("/gstr1")
def gstr1(access: GstView, db: DB, date_from: date, date_to: date, gstin: str | None = GSTIN_Q) -> dict:
    _period(date_from, date_to)
    return reports.gstr1(db, access.business.id, date_from, date_to, gstin)


@router.get("/gstr3b")
def gstr3b(access: GstView, db: DB, date_from: date, date_to: date, gstin: str | None = GSTIN_Q) -> dict:
    _period(date_from, date_to)
    return reports.gstr3b(db, access.business.id, date_from, date_to, gstin)

