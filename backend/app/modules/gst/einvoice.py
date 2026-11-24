"""E-invoice payload (NIC IRP schema 1.1) built from a finalized document.

The JSON can be uploaded through the e-invoice portal's bulk tool, or sent by a GSP connector
(Phase 5). The IRN, acknowledgement and signed QR come back from the IRP and are recorded with
``service.record_irn`` — this module never generates them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.errors import ValidationFailed
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document

_DOC_CODE = {DocType.SALES_INVOICE: "INV", DocType.CREDIT_NOTE: "CRN", DocType.DEBIT_NOTE: "DBN"}


def _n(d: Decimal) -> float:
    # The IRP schema uses JSON numbers. Values are already rounded to paise, so the conversion
    # is exact for display; the ERP itself never computes with these floats.
    return float(d)


def _addr(a: dict[str, Any] | None) -> dict[str, Any]:
    a = a or {}
    return {"Addr1": (a.get("address_line1") or "")[:100], "Addr2": (a.get("address_line2") or "")[:100] or None,
            "Loc": (a.get("city") or "")[:50], "Pin": int(a["pincode"]) if a.get("pincode") else None,
            "Stcd": a.get("state_code")}


def build(doc: Document) -> dict[str, Any]:
    if doc.doc_type not in _DOC_CODE or doc.status != DocStatus.FINAL:
        raise ValidationFailed("E-invoices are generated for finalized invoices and credit/debit notes")
    seller = doc.seller_snapshot or {}
    buyer = doc.party_snapshot or {}
    gst_type = buyer.get("gst_type")
    if gst_type == "overseas":
        sup = "EXPWP" if doc.igst_total > 0 else "EXPWOP"
    elif gst_type == "sez":
        sup = "SEZWP" if doc.igst_total > 0 else "SEZWOP"
    else:
        sup = "B2B"
    missing = []
    if not seller.get("gstin"):
        missing.append("seller GSTIN")
    if sup == "B2B" and not buyer.get("gstin"):
        missing.append("buyer GSTIN (e-invoices are for B2B, export and SEZ supplies)")
    seller_addr = {k: seller.get(k) for k in ("address_line1", "address_line2", "city",
                                              "pincode", "state_code")}
    if not seller_addr.get("address_line1") or not seller_addr.get("pincode"):
        missing.append("seller address and PIN code")
    bill = buyer.get("billing_address") or {}
    if sup == "B2B" and (not bill.get("address_line1") or not bill.get("pincode")):
        missing.append("buyer billing address and PIN code")
    if missing:
        raise ValidationFailed("Cannot build the e-invoice: missing " + ", ".join(missing),
                               details={"missing": missing})

    items = []
    for ln in doc.lines:
        items.append({
            "SlNo": str(ln.line_no), "PrdDesc": ln.description[:300],
            "IsServc": "Y" if ln.is_service else "N", "HsnCd": ln.hsn_sac or "",
            "Qty": _n(ln.quantity), "Unit": ln.uqc or "OTH", "UnitPrice": _n(ln.unit_price),
            "TotAmt": _n(ln.gross_amount), "Discount": _n(ln.discount_total),
            "AssAmt": _n(ln.taxable_value), "GstRt": _n(ln.gst_rate),
            "IgstAmt": _n(ln.igst_amount), "CgstAmt": _n(ln.cgst_amount),
            "SgstAmt": _n(ln.sgst_amount), "CesRt": _n(ln.cess_rate), "CesAmt": _n(ln.cess_amount),
            "TotItemVal": _n(ln.line_total),
        })
    return {
        "Version": "1.1",
        "TranDtls": {"TaxSch": "GST", "SupTyp": sup, "RegRev": "Y" if doc.reverse_charge else "N",
                     "IgstOnIntra": "N"},
        "DocDtls": {"Typ": _DOC_CODE[doc.doc_type], "No": doc.number,
                    "Dt": doc.doc_date.strftime("%d/%m/%Y")},
        "SellerDtls": {"Gstin": seller["gstin"], "LglNm": seller.get("legal_name") or seller.get("name"),
                       **_addr(seller_addr)},
        "BuyerDtls": {"Gstin": buyer.get("gstin") or "URP", "LglNm": buyer.get("name"),
                      "Pos": doc.place_of_supply if sup == "B2B" else "96", **_addr(bill)},
        "ItemList": items,
        "ValDtls": {"AssVal": _n(doc.taxable_total), "CgstVal": _n(doc.cgst_total),
                    "SgstVal": _n(doc.sgst_total), "IgstVal": _n(doc.igst_total),
                    "CesVal": _n(doc.cess_total), "RndOffAmt": _n(doc.round_off),
                    "TotInvVal": _n(doc.grand_total)},
    }
