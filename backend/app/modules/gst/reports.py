"""GST registers and return preparation from finalized documents.

These are *prepared* figures for review and for filing through the GST portal or an authorised
GSP. Nothing here files a return, and no response claims that one was filed.

Sign convention: credit notes / purchase returns reduce totals; registers show them as
negative rows so every column sums correctly.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document
from app.modules.businesses.models import Branch, Business
from app.modules.gst.tax import ZERO, TaxCategory

SALES_TYPES = (DocType.SALES_INVOICE, DocType.CREDIT_NOTE, DocType.DEBIT_NOTE)
PURCHASE_TYPES = (DocType.PURCHASE_INVOICE, DocType.PURCHASE_RETURN)
NEGATIVE = (DocType.CREDIT_NOTE, DocType.PURCHASE_RETURN)
B2CL_LIMIT = Decimal("100000")  # inter-state B2C invoices above ₹1 lakh are reported invoice-wise
REGISTERED = ("regular", "composition", "sez")


def present(obj: Any, key: str = "") -> Any:
    """Return figures as filed: amounts in rupees and paise, quantities to 3 places, rates as is."""
    if isinstance(obj, dict):
        return {k: present(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [present(v, key) for v in obj]
    if isinstance(obj, Decimal):
        if key == "rate":
            return obj
        return obj.quantize(Decimal("0.001") if key == "quantity" else Decimal("0.01"))
    return obj


def _sign(doc: Document) -> Decimal:
    return Decimal(-1) if doc.doc_type in NEGATIVE else Decimal(1)


def documents(db: Session, business_id: uuid.UUID, types: tuple[DocType, ...], date_from: date,
              date_to: date, gstin: str | None, *, include_cancelled: bool = False) -> list[Document]:
    stmt = select(Document).where(
        Document.business_id == business_id, Document.doc_type.in_(types),
        Document.doc_date >= date_from, Document.doc_date <= date_to,
        Document.status.in_([DocStatus.FINAL, DocStatus.CANCELLED] if include_cancelled
                            else [DocStatus.FINAL]))
    if gstin:
        if types == PURCHASE_TYPES:
            # We are the buyer: match the receiving branch's own GSTIN, or the business GSTIN
            # for branches registered under it.
            business = db.get(Business, business_id)
            branch_ids = [b.id for b in db.scalars(select(Branch).where(Branch.business_id == business_id))
                          if (b.gstin or (business.gstin if business else None)) == gstin]
            stmt = stmt.where(Document.branch_id.in_(branch_ids))
        else:
            stmt = stmt.where(Document.seller_gstin == gstin)
    return list(db.scalars(stmt.order_by(Document.doc_date, Document.number)))


def _party(doc: Document) -> dict[str, Any]:
    return doc.party_snapshot or {}


def _registered(doc: Document) -> bool:
    p = _party(doc)
    return bool(p.get("gstin")) and p.get("gst_type") in REGISTERED


def _export(doc: Document) -> bool:
    return _party(doc).get("gst_type") == "overseas"


# ---------------------------------------------------------------------------- registers


def register(db: Session, business_id: uuid.UUID, side: str, date_from: date, date_to: date,
             gstin: str | None) -> dict[str, Any]:
    types = SALES_TYPES if side == "sales" else PURCHASE_TYPES
    rows, totals = [], defaultdict(lambda: ZERO)
    for d in documents(db, business_id, types, date_from, date_to, gstin):
        s = _sign(d)
        row = {
            "document_id": str(d.id), "doc_type": d.doc_type.value, "number": d.number,
            "doc_date": d.doc_date.isoformat(), "party": _party(d).get("name"),
            "party_gstin": _party(d).get("gstin") if side == "sales" else d.seller_gstin,
            "supplier_invoice_no": d.supplier_invoice_no, "place_of_supply": d.place_of_supply,
            "reverse_charge": d.reverse_charge,
            "taxable_value": s * d.taxable_total, "cgst": s * d.cgst_total, "sgst": s * d.sgst_total,
            "igst": s * d.igst_total, "cess": s * d.cess_total, "total": s * d.grand_total,
        }
        rows.append(row)
        for k in ("taxable_value", "cgst", "sgst", "igst", "cess", "total"):
            totals[k] += row[k]
    return present({"side": side, "from": date_from.isoformat(), "to": date_to.isoformat(),
                    "rows": rows, "totals": dict(totals)})


# ---------------------------------------------------------------------------- HSN summary


@dataclass
class _Bucket:
    quantity: Decimal = ZERO
    taxable_value: Decimal = ZERO
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO
    total: Decimal = ZERO
    extra: dict[str, Any] = field(default_factory=dict)

    def add(self, s: Decimal, ln: Any, qty: bool = True) -> None:
        if qty:
            self.quantity += s * ln.quantity
        self.taxable_value += s * ln.taxable_value
        self.igst += s * ln.igst_amount
        self.cgst += s * ln.cgst_amount
        self.sgst += s * ln.sgst_amount
        self.cess += s * ln.cess_amount
        self.total += s * ln.line_total

    def out(self) -> dict[str, Any]:
        return {**self.extra, "quantity": self.quantity, "taxable_value": self.taxable_value,
                "igst": self.igst, "cgst": self.cgst, "sgst": self.sgst, "cess": self.cess,
                "total": self.total}


def hsn_summary(docs: list[Document]) -> dict[str, list[dict[str, Any]]]:
    """Table 12 of GSTR-1, split into B2B and B2C as the portal requires."""
    out: dict[str, dict[tuple, _Bucket]] = {"b2b": {}, "b2c": {}}
    for d in docs:
        s = _sign(d)
        part = "b2b" if _registered(d) else "b2c"
        for ln in d.lines:
            key = (ln.hsn_sac or "", ln.uqc or "OTH", ln.gst_rate)
            b = out[part].setdefault(key, _Bucket(extra={
                "hsn_sac": key[0], "uqc": key[1], "rate": key[2], "description": ln.description}))
            # Value-only credit notes (no goods returned) change value, not quantity.
            b.add(s, ln, qty=not (d.doc_type == DocType.CREDIT_NOTE and not d.restock) and not ln.is_service)
    return present({k: [b.out() for _, b in sorted(v.items())] for k, v in out.items()})


# ---------------------------------------------------------------------------- GSTR-1


def _rate_items(d: Document) -> list[dict[str, Any]]:
    by_rate: dict[Decimal, _Bucket] = defaultdict(_Bucket)
    for ln in d.lines:
        if ln.tax_category in (TaxCategory.TAXABLE, TaxCategory.ZERO_RATED):
            by_rate[ln.gst_rate].add(Decimal(1), ln, qty=False)
    return [{"rate": r, "taxable_value": b.taxable_value, "igst": b.igst, "cgst": b.cgst,
             "sgst": b.sgst, "cess": b.cess} for r, b in sorted(by_rate.items())]


def _invoice(d: Document) -> dict[str, Any]:
    return {"number": d.number, "date": d.doc_date.isoformat(), "value": d.grand_total,
            "place_of_supply": d.place_of_supply, "reverse_charge": d.reverse_charge,
            "items": _rate_items(d)}


def gstr1(db: Session, business_id: uuid.UUID, date_from: date, date_to: date,
          gstin: str | None) -> dict[str, Any]:
    docs = documents(db, business_id, SALES_TYPES, date_from, date_to, gstin)
    b2b: dict[str, list] = defaultdict(list)
    b2cl: list[dict] = []
    b2cs: dict[tuple, _Bucket] = {}
    exp: list[dict] = []
    cdnr: dict[str, list] = defaultdict(list)
    cdnur: list[dict] = []
    nil = {k: {"nil_rated": ZERO, "exempt": ZERO, "non_gst": ZERO}
           for k in ("inter_registered", "intra_registered", "inter_unregistered", "intra_unregistered")}
    refs = {d.id: d for d in docs}

    for d in docs:
        reg, s = _registered(d), _sign(d)
        nil_key = f"{'inter' if d.is_interstate else 'intra'}_{'registered' if reg else 'unregistered'}"
        for ln in d.lines:
            if ln.tax_category in (TaxCategory.NIL_RATED, TaxCategory.EXEMPT, TaxCategory.NON_GST):
                nil[nil_key][ln.tax_category.value] += s * ln.taxable_value
        if d.doc_type == DocType.SALES_INVOICE:
            if _export(d):
                exp.append({**_invoice(d), "export_type": "WPAY" if d.igst_total > 0 else "WOPAY"})
            elif reg:
                kind = "SEZ" if _party(d).get("gst_type") == "sez" else "R"
                b2b[_party(d)["gstin"]].append({**_invoice(d), "invoice_type": kind})
            elif d.is_interstate and d.grand_total > B2CL_LIMIT:
                b2cl.append(_invoice(d))
            else:
                for item in _rate_items(d):
                    key = (d.place_of_supply, item["rate"], "INTER" if d.is_interstate else "INTRA")
                    b = b2cs.setdefault(key, _Bucket(extra={"place_of_supply": key[0], "rate": key[1],
                                                            "supply_type": key[2]}))
                    for k in ("taxable_value", "igst", "cgst", "sgst", "cess"):
                        setattr(b, k, getattr(b, k) + item[k])
            continue
        # credit / debit notes
        original = refs.get(d.reference_document_id) or (
            db.get(Document, d.reference_document_id) if d.reference_document_id else None)
        note = {**_invoice(d), "note_type": "C" if d.doc_type == DocType.CREDIT_NOTE else "D",
                "original_number": original.number if original else None,
                "original_date": original.doc_date.isoformat() if original else None}
        if reg:
            cdnr[_party(d)["gstin"]].append(note)
        elif _export(d) or (original is not None and original.is_interstate
                            and original.grand_total > B2CL_LIMIT):
            cdnur.append({**note, "ur_type": "EXPWP" if _export(d) else "B2CL"})
        else:  # notes against small B2C sales reduce the B2CS figures
            for item in _rate_items(d):
                key = (d.place_of_supply, item["rate"], "INTER" if d.is_interstate else "INTRA")
                b = b2cs.setdefault(key, _Bucket(extra={"place_of_supply": key[0], "rate": key[1],
                                                        "supply_type": key[2]}))
                for k in ("taxable_value", "igst", "cgst", "sgst", "cess"):
                    setattr(b, k, getattr(b, k) + s * item[k])

    all_docs = documents(db, business_id, SALES_TYPES, date_from, date_to, gstin, include_cancelled=True)
    issued: dict[str, dict[str, Any]] = {}
    for d in all_docs:
        x = issued.setdefault(d.doc_type.value, {"doc_type": d.doc_type.value, "from": d.number,
                                                 "to": d.number, "total": 0, "cancelled": 0})
        x["from"], x["to"] = min(x["from"], d.number), max(x["to"], d.number)
        x["total"] += 1
        x["cancelled"] += int(d.status == DocStatus.CANCELLED)

    return present({
        "status": "prepared — not filed",
        "gstin": gstin, "from": date_from.isoformat(), "to": date_to.isoformat(),
        "b2b": [{"ctin": k, "invoices": v} for k, v in sorted(b2b.items())],
        "b2cl": b2cl,
        "b2cs": [b.out() | {"quantity": None} for _, b in sorted(b2cs.items())],
        "exp": exp,
        "cdnr": [{"ctin": k, "notes": v} for k, v in sorted(cdnr.items())],
        "cdnur": cdnur,
        "nil": nil,
        "hsn": hsn_summary(docs),
        "doc_issue": list(issued.values()),
    })


# ---------------------------------------------------------------------------- GSTR-3B


def _heads() -> dict[str, Decimal]:
    return {"taxable_value": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "cess": ZERO}


def _add(h: dict[str, Decimal], s: Decimal, ln: Any, tax: bool = True) -> None:
    h["taxable_value"] += s * ln.taxable_value
    if tax:
        h["igst"] += s * ln.igst_amount
        h["cgst"] += s * ln.cgst_amount
        h["sgst"] += s * ln.sgst_amount
        h["cess"] += s * ln.cess_amount


def gstr3b(db: Session, business_id: uuid.UUID, date_from: date, date_to: date,
           gstin: str | None) -> dict[str, Any]:
    out_taxable, zero_rated, nil_exempt, non_gst = _heads(), _heads(), _heads(), _heads()
    for d in documents(db, business_id, SALES_TYPES, date_from, date_to, gstin):
        s = _sign(d)
        for ln in d.lines:
            if ln.tax_category in (TaxCategory.NIL_RATED, TaxCategory.EXEMPT):
                _add(nil_exempt, s, ln, tax=False)
            elif ln.tax_category == TaxCategory.NON_GST:
                _add(non_gst, s, ln, tax=False)
            elif ln.tax_category == TaxCategory.ZERO_RATED or _export(d) or \
                    _party(d).get("gst_type") == "sez":
                _add(zero_rated, s, ln)
            elif not d.reverse_charge:  # tax on our RCM sales is paid by the buyer
                _add(out_taxable, s, ln)

    rcm_inward, itc_rcm, itc_other, ineligible, exempt_inward = (
        _heads(), _heads(), _heads(), _heads(), _heads())
    for d in documents(db, business_id, PURCHASE_TYPES, date_from, date_to, gstin):
        s = _sign(d)
        for ln in d.lines:
            if ln.tax_category in (TaxCategory.NIL_RATED, TaxCategory.EXEMPT, TaxCategory.NON_GST) \
                    or (ln.igst_amount + ln.cgst_amount + ln.sgst_amount == 0 and not d.reverse_charge):
                _add(exempt_inward, s, ln, tax=False)
                continue
            if d.reverse_charge:
                _add(rcm_inward, s, ln)
            target = (itc_rcm if d.reverse_charge else itc_other) if ln.itc_eligible else ineligible
            _add(target, s, ln)

    def tax_only(h: dict[str, Decimal]) -> dict[str, Decimal]:
        return {k: v for k, v in h.items() if k != "taxable_value"}

    liability = {k: out_taxable[k] + zero_rated[k] + rcm_inward[k] for k in ("igst", "cgst", "sgst", "cess")}
    itc = {k: itc_rcm[k] + itc_other[k] for k in ("igst", "cgst", "sgst", "cess")}
    return present({
        "status": "prepared — not filed",
        "gstin": gstin, "from": date_from.isoformat(), "to": date_to.isoformat(),
        "3.1": {"a_outward_taxable": out_taxable, "b_outward_zero_rated": zero_rated,
                "c_outward_nil_exempt": nil_exempt, "d_inward_reverse_charge": rcm_inward,
                "e_non_gst": non_gst},
        "4": {"a3_itc_inward_reverse_charge": tax_only(itc_rcm),
              "a5_itc_all_other": tax_only(itc_other), "net_itc_available": itc,
              "d_ineligible_itc": tax_only(ineligible)},
        "5": {"exempt_nil_non_gst_inward": exempt_inward["taxable_value"]},
        "summary": {"output_tax": liability, "input_tax_credit": itc,
                    "note": "Set-off order and cash/credit split are applied on the GST portal."},
    })
