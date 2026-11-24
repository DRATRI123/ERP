"""E-way bills.

The ERP does not talk to the NIC portal directly (that needs a GSP and API credentials).
Instead it:

1. lists the documents that need an e-way bill (goods worth more than the threshold, ₹50,000
   by default, leaving on a final sales invoice, delivery challan or purchase return);
2. builds the portal's **bulk-generation JSON** for the chosen ones (Upload on
   ewaybillgst.gov.in → Generate → Bulk), with transport details;
3. records the e-way bill number the portal returns, works out its validity (1 day per 200 km
   for regular cargo, counted to midnight) and prints it on the invoice.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document

THRESHOLD = Decimal(50000)
TYPES = {DocType.SALES_INVOICE: ("O", 1, "INV"), DocType.DELIVERY_CHALLAN: ("O", 4, "CHL"),
         DocType.PURCHASE_RETURN: ("O", 8, "INV")}  # supplyType, subSupplyType (1 supply, 4 job work/other, 8 others), docType
MODES = {"road": 1, "rail": 2, "air": 3, "ship": 4}
VEHICLE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$|^TMP[A-Z0-9]{5,12}$")
EWB_NO = re.compile(r"^[0-9]{12}$")


def _goods_value(doc: Document) -> Decimal:
    """Taxable value of goods plus their tax (services do not travel)."""
    return sum((ln.taxable_value + ln.cgst_amount + ln.sgst_amount + ln.igst_amount + ln.cess_amount
                for ln in doc.lines if not ln.is_service), Decimal(0))


def pending(db: Session, access: BusinessAccess, *, threshold: Decimal = THRESHOLD, days: int = 30) -> list[dict[str, Any]]:
    access.require("gst.view")
    since = datetime.now(UTC).date() - timedelta(days=days)
    docs = db.scalars(select(Document).where(
        Document.business_id == access.business.id, Document.doc_type.in_(list(TYPES)), Document.status == DocStatus.FINAL,
        Document.eway_bill_no.is_(None), Document.doc_date >= since).order_by(Document.doc_date.desc()))
    out = []
    for d in docs:
        if not access.can_access_branch(d.branch_id):
            continue
        value = _goods_value(d)
        if value > threshold:
            out.append({"id": d.id, "number": d.number, "doc_type": d.doc_type.value, "doc_date": d.doc_date,
                        "party_name": d.party_name, "goods_value": value, "is_interstate": d.is_interstate})
    return out


def _addr(snapshot: dict[str, Any] | None, *, ship: bool = False) -> dict[str, Any]:
    snap = snapshot or {}
    a = (snap.get("shipping_address") if ship else None) or snap.get("billing_address") or snap
    return {"addr1": (a.get("address_line1") or "")[:120], "addr2": (a.get("address_line2") or "")[:120],
            "place": (a.get("city") or "")[:50], "pincode": a.get("pincode"), "state": a.get("state_code") or snap.get("state_code")}


def _r(v: Decimal) -> float:
    return float(Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def bulk_json(db: Session, access: BusinessAccess, ids: list[uuid.UUID], *, distance_km: int, vehicle_no: str | None,
              transporter_id: str | None, transporter_name: str | None, mode: str = "road") -> dict[str, Any]:
    access.require("gst.view")
    if not 0 < distance_km <= 4000:
        raise ValidationFailed("Distance must be 1–4000 km")
    if mode not in MODES:
        raise ValidationFailed("Mode must be road, rail, air or ship")
    vehicle = (vehicle_no or "").replace(" ", "").replace("-", "").upper() or None
    if mode == "road" and not vehicle and not transporter_id:
        raise ValidationFailed("By road, give the vehicle number or the transporter's ID")
    if vehicle and not VEHICLE.match(vehicle):
        raise ValidationFailed(f"{vehicle_no} is not a valid vehicle number (e.g. MH12AB1234)")
    bills = []
    for doc_id in ids:
        d = db.get(Document, doc_id)
        if d is None or d.business_id != access.business.id or d.doc_type not in TYPES or d.status != DocStatus.FINAL:
            raise ValidationFailed("Choose final sales invoices, delivery challans or purchase returns")
        if d.eway_bill_no:
            raise Conflict(f"{d.number} already has e-way bill {d.eway_bill_no}")
        seller, buyer = d.seller_snapshot or {}, d.party_snapshot or {}
        frm, to = _addr(seller), _addr(buyer, ship=True)
        missing = [k for k, v in (("our PIN code", frm["pincode"]), ("customer's PIN code", to["pincode"])) if not v]
        if missing:
            raise ValidationFailed(f"{d.number}: add {' and '.join(missing)} first")
        supply, sub, doc_kind = TYPES[d.doc_type]
        goods = [ln for ln in d.lines if not ln.is_service]
        main_hsn = max(goods, key=lambda ln: ln.taxable_value).hsn_sac if goods else None
        bills.append({
            "userGstin": seller.get("gstin"), "supplyType": supply, "subSupplyType": sub, "docType": doc_kind,
            "docNo": d.number, "docDate": f"{d.doc_date:%d/%m/%Y}", "transType": 1,
            "fromGstin": seller.get("gstin") or "URP", "fromTrdName": (seller.get("legal_name") or seller.get("name") or "")[:100],
            "fromAddr1": frm["addr1"], "fromAddr2": frm["addr2"], "fromPlace": frm["place"], "fromPincode": int(frm["pincode"]),
            "fromStateCode": int(seller.get("state_code") or frm["state"] or 0), "actFromStateCode": int(frm["state"] or seller.get("state_code") or 0),
            "toGstin": buyer.get("gstin") or "URP", "toTrdName": (buyer.get("name") or d.party_name or "")[:100],
            "toAddr1": to["addr1"], "toAddr2": to["addr2"], "toPlace": to["place"], "toPincode": int(to["pincode"]),
            "toStateCode": int(d.place_of_supply or to["state"] or 0), "actToStateCode": int(to["state"] or d.place_of_supply or 0),
            "totalValue": _r(sum((ln.taxable_value for ln in goods), Decimal(0))),
            "cgstValue": _r(sum((ln.cgst_amount for ln in goods), Decimal(0))), "sgstValue": _r(sum((ln.sgst_amount for ln in goods), Decimal(0))),
            "igstValue": _r(sum((ln.igst_amount for ln in goods), Decimal(0))), "cessValue": _r(sum((ln.cess_amount for ln in goods), Decimal(0))),
            "cessNonAdvolValue": 0, "otherValue": _r(d.grand_total - _goods_value(d)), "totInvValue": _r(d.grand_total),
            "transMode": MODES[mode], "transDistance": distance_km, "transporterId": transporter_id or "",
            "transporterName": transporter_name or "", "transDocNo": "", "transDocDate": "",
            "vehicleNo": vehicle or "", "vehicleType": "R", "mainHsnCode": int(main_hsn) if main_hsn and main_hsn.isdigit() else None,
            "itemList": [{"itemNo": i, "productName": ln.description[:100], "productDesc": ln.description[:100],
                          "hsnCode": int(ln.hsn_sac) if ln.hsn_sac and ln.hsn_sac.isdigit() else None,
                          "quantity": float(ln.quantity), "qtyUnit": ln.uqc or "OTH", "taxableAmount": _r(ln.taxable_value),
                          "cgstRate": float(ln.gst_rate / 2) if ln.cgst_amount else 0, "sgstRate": float(ln.gst_rate / 2) if ln.sgst_amount else 0,
                          "igstRate": float(ln.gst_rate) if ln.igst_amount else 0, "cessRate": float(ln.cess_rate), "cessNonAdvol": 0}
                         for i, ln in enumerate(goods, 1)],
        })
        d.vehicle_no, d.transporter_id, d.transporter_name, d.transport_distance_km = vehicle, transporter_id, transporter_name, distance_km
    db.flush()
    return {"version": "1.0.0621", "billLists": bills}


def validity(generated: datetime, distance_km: int) -> datetime:
    """Regular cargo: 1 day per 200 km (or part); each day ends at midnight after the first 24 h."""
    days = max(1, -(-distance_km // 200))
    tz = ZoneInfo(get_settings().timezone)
    local = generated.astimezone(tz)
    end_day = (local + timedelta(days=days)).date()
    return datetime.combine(end_day, time(23, 59, 59), tzinfo=tz).astimezone(UTC)


def record(db: Session, access: BusinessAccess, doc: Document, *, eway_bill_no: str, generated_at: datetime,
           distance_km: int | None, vehicle_no: str | None) -> Document:
    access.require("invoices.edit" if doc.doc_type != DocType.PURCHASE_RETURN else "purchases.edit")
    if doc.doc_type not in TYPES or doc.status != DocStatus.FINAL:
        raise ValidationFailed("E-way bills are recorded on final invoices, challans and purchase returns")
    no = eway_bill_no.replace(" ", "")
    if not EWB_NO.match(no):
        raise ValidationFailed("An e-way bill number has 12 digits")
    if doc.eway_bill_no and doc.eway_bill_no != no:
        raise Conflict(f"{doc.number} already has e-way bill {doc.eway_bill_no}")
    if vehicle_no:
        v = vehicle_no.replace(" ", "").replace("-", "").upper()
        if not VEHICLE.match(v):
            raise ValidationFailed(f"{vehicle_no} is not a valid vehicle number")
        doc.vehicle_no = v
    if distance_km:
        doc.transport_distance_km = distance_km
    if not doc.transport_distance_km:
        raise ValidationFailed("Give the distance in km (it sets the validity)")
    doc.eway_bill_no, doc.eway_bill_date = no, generated_at
    doc.eway_valid_until = validity(generated_at, doc.transport_distance_km)
    db.flush()
    audit.record(db, action="document.eway_bill_recorded", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"eway_bill_no": no, "valid_until": doc.eway_valid_until})
    return doc

