"""GST registers and return preparation. Figures are prepared for filing; nothing is filed."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

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



# ---------------------------------------------------------------------------- e-way bills


class EwayJsonIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    distance_km: int = Field(ge=1, le=4000)
    mode: str = Field("road", pattern="^(road|rail|air|ship)$")
    vehicle_no: str | None = Field(None, max_length=15)
    transporter_id: str | None = Field(None, pattern=r"^[0-9A-Z]{15}$")
    transporter_name: str | None = Field(None, max_length=100)


class EwayRecordIn(BaseModel):
    eway_bill_no: str = Field(min_length=12, max_length=14)
    generated_at: datetime | None = None
    distance_km: int | None = Field(None, ge=1, le=4000)
    vehicle_no: str | None = Field(None, max_length=15)


@router.get("/eway-bills/pending")
def eway_pending(access: GstView, db: DB, days: int = Query(30, ge=1, le=180)) -> list[dict[str, Any]]:
    """Final invoices, challans and purchase returns moving goods worth over ₹50,000 with no e-way bill yet."""
    from app.modules.gst import ewaybill

    return ewaybill.pending(db, access, days=days)


@router.post("/eway-bills/json")
def eway_json(body: EwayJsonIn, access: GstView, db: DB) -> Response:
    """The portal's bulk-generation JSON (ewaybillgst.gov.in → Generate → Bulk)."""
    import json

    from app.modules.gst import ewaybill

    data = ewaybill.bulk_json(db, access, body.document_ids, distance_km=body.distance_km, vehicle_no=body.vehicle_no,
                              transporter_id=body.transporter_id, transporter_name=body.transporter_name, mode=body.mode)
    db.commit()
    return Response(json.dumps(data, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="eway-bills.json"'})


@router.post("/eway-bills/{document_id}")
def eway_record(document_id: uuid.UUID, body: EwayRecordIn, access: Annotated[BusinessAccess, Depends(require("gst.view"))],
                db: DB) -> dict[str, Any]:
    """Record the number the portal gave; validity is worked out from the distance."""
    from datetime import UTC

    from app.modules.billing.service import get_document
    from app.modules.gst import ewaybill

    doc = ewaybill.record(db, access, get_document(db, access, document_id), eway_bill_no=body.eway_bill_no,
                          generated_at=body.generated_at or datetime.now(UTC), distance_km=body.distance_km,
                          vehicle_no=body.vehicle_no)
    db.commit()
    return {"id": doc.id, "number": doc.number, "eway_bill_no": doc.eway_bill_no, "eway_valid_until": doc.eway_valid_until,
            "vehicle_no": doc.vehicle_no}
