"""GST compliance beyond monthly returns: GSTR-2B reconciliation, the GSTR-9 annual summary,
CMP-08 / GSTR-4 for composition dealers, RCM self-invoices, job work (ITC-04) and the
section 194Q purchase-TDS tracker."""

from __future__ import annotations

import html
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document
from app.modules.businesses.models import FinancialYear
from app.modules.gst import reports
from app.modules.gst.models import JobWorkChallan, JobWorkDirection, JobWorkNature, RcmSelfInvoice
from app.modules.gst.words import rupees_in_words
from app.modules.numbering.service import next_number
from app.modules.parties.models import Party, PartyGstType

ZERO = Decimal(0)
TOLERANCE = Decimal(1)  # rupees: rounding differences between our books and the supplier's return


def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v or 0)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001 - a malformed number in the portal file
        return ZERO


def norm_invoice_no(no: str | None) -> str:
    """"INV/2026-27/0012" and "inv-2026-27-12" both -> "INV20262712": suppliers' numbers are typed differently."""
    parts = re.findall(r"[A-Z]+|\d+", (no or "").upper())
    return "".join(str(int(p)) if p.isdigit() else p for p in parts)


def _fy_of(db: Session, business_id: uuid.UUID, d: date) -> FinancialYear:
    fy = db.scalar(select(FinancialYear).where(FinancialYear.business_id == business_id,
                                               FinancialYear.start_date <= d, FinancialYear.end_date >= d))
    if fy is None:
        raise ValidationFailed(f"No financial year covers {d.isoformat()}")
    return fy


# ============================================================================ GSTR-2B reconciliation


def parse_2b(data: dict[str, Any]) -> list[dict[str, Any]]:
    """B2B invoices (and B2B credit/debit notes) from the GSTR-2B JSON downloaded from the portal."""
    root = data.get("data", data)
    docdata = root.get("docdata", root)
    out: list[dict[str, Any]] = []
    for kind, key, inv_key in (("invoice", "b2b", "inv"), ("note", "cdnr", "nt")):
        for sup in docdata.get(key, []) or []:
            gstin = (sup.get("ctin") or "").upper()
            for inv in sup.get(inv_key, []) or []:
                items = inv.get("items") or inv.get("itms") or []
                tax = {k: sum((_d(i.get(k)) for i in items), ZERO) for k in ("txval", "igst", "cgst", "sgst", "cess")}
                raw_date = inv.get("dt") or inv.get("idt") or inv.get("ntdt") or ""
                m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", raw_date)
                try:
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
                except ValueError:
                    d = None
                sign = Decimal(-1) if kind == "note" and (inv.get("typ") or inv.get("ntty")) == "C" else Decimal(1)
                out.append({"kind": kind, "supplier_gstin": gstin, "supplier_name": sup.get("trdnm"),
                            "number": inv.get("inum") or inv.get("ntnum") or "", "date": d,
                            "value": _d(inv.get("val")), "taxable": sign * tax["txval"], "igst": sign * tax["igst"],
                            "cgst": sign * tax["cgst"], "sgst": sign * tax["sgst"], "cess": sign * tax["cess"],
                            "itc_available": (inv.get("itcavl") or "Y") != "N", "reverse_charge": inv.get("rev") == "Y"})
    if not out and not docdata:
        raise ValidationFailed("This does not look like a GSTR-2B file (expected data → docdata → b2b)")
    return out


def reconcile_2b(db: Session, access: BusinessAccess, *, data: dict[str, Any], date_from: date, date_to: date) -> dict[str, Any]:
    """Match the portal's GSTR-2B against our purchase bills of the period.

    * matched — same supplier GSTIN and bill number, taxable value and tax within ₹1;
    * mismatch — found, but the amounts differ;
    * only_in_2b — the supplier filed it, we have not recorded it (missing purchase or wrong number);
    * only_in_books — we recorded it, the supplier has not filed it: that ITC is at risk.
    """
    access.require("gst.view")
    portal = [r for r in parse_2b(data) if r["kind"] == "invoice"]
    bills = list(db.scalars(select(Document).where(
        Document.business_id == access.business.id, Document.doc_type == DocType.PURCHASE_INVOICE,
        Document.status == DocStatus.FINAL, Document.doc_date >= date_from, Document.doc_date <= date_to)))
    ours: dict[tuple[str, str], Document] = {}
    for bill in bills:
        gstin = (bill.seller_gstin or "").upper()
        if gstin:
            ours[(gstin, norm_invoice_no(bill.supplier_invoice_no))] = bill
    rows: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    for p in portal:
        key = (p["supplier_gstin"], norm_invoice_no(p["number"]))
        b: Document | None = ours.get(key)
        entry = {"supplier_gstin": p["supplier_gstin"], "supplier_name": p["supplier_name"], "number": p["number"],
                 "date": p["date"], "portal_taxable": p["taxable"], "portal_tax": p["igst"] + p["cgst"] + p["sgst"] + p["cess"],
                 "itc_available": p["itc_available"], "document_id": None, "our_number": None,
                 "books_taxable": None, "books_tax": None}
        if b is None:
            entry["status"] = "only_in_2b"
        else:
            seen.add(b.id)
            books_tax = b.igst_total + b.cgst_total + b.sgst_total + b.cess_total
            entry.update({"document_id": b.id, "our_number": b.number, "books_taxable": b.taxable_total, "books_tax": books_tax})
            close = abs(b.taxable_total - p["taxable"]) <= TOLERANCE and abs(books_tax - entry["portal_tax"]) <= TOLERANCE
            entry["status"] = "matched" if close else "mismatch"
        rows.append(entry)
    for b in bills:
        if b.id in seen or not b.seller_gstin:
            continue
        party = db.get(Party, b.party_id) if b.party_id else None
        rows.append({"status": "only_in_books", "supplier_gstin": b.seller_gstin, "supplier_name": party.name if party else None,
                     "number": b.supplier_invoice_no, "date": b.supplier_invoice_date or b.doc_date, "portal_taxable": None,
                     "portal_tax": None, "itc_available": None, "document_id": b.id, "our_number": b.number,
                     "books_taxable": b.taxable_total, "books_tax": b.igst_total + b.cgst_total + b.sgst_total + b.cess_total})
    summary: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = summary.setdefault(r["status"], {"count": 0, "tax": ZERO})
        s["count"] += 1
        s["tax"] += r["books_tax"] if r["status"] == "only_in_books" else (r["portal_tax"] or ZERO)
    order = {"only_in_books": 0, "mismatch": 1, "only_in_2b": 2, "matched": 3}
    rows.sort(key=lambda r: (order[r["status"]], r["supplier_gstin"] or "", str(r["number"])))
    return {"date_from": date_from, "date_to": date_to, "summary": summary, "rows": rows,
            "itc_at_risk": summary.get("only_in_books", {}).get("tax", ZERO)}


# ============================================================================ annual returns


def gstr9(db: Session, access: BusinessAccess, *, fy_start: date) -> dict[str, Any]:
    """GSTR-9 key tables for a financial year, from the books (the portal pre-fills from your
    GSTR-1 / 3B; compare with this before filing)."""
    access.require("gst.view")
    fy = _fy_of(db, access.business.id, fy_start)
    g3 = reports.gstr3b(db, access.business.id, fy.start_date, fy.end_date, None)
    g1 = reports.gstr1(db, access.business.id, fy.start_date, fy.end_date, None)
    docs = reports.documents(db, access.business.id, reports.SALES_TYPES, fy.start_date, fy.end_date, None)
    return {"financial_year": fy.code, "from": fy.start_date, "to": fy.end_date,
            "table_4_outward_taxable": g3["3.1"]["a_outward_taxable"], "table_4_zero_rated": g3["3.1"]["b_outward_zero_rated"],
            "table_4_inward_rcm": g3["3.1"]["d_inward_reverse_charge"],
            "table_5_nil_exempt": g3["3.1"]["c_outward_nil_exempt"], "table_5_non_gst": g3["3.1"]["e_non_gst"],
            "table_6_itc": g3["4"], "table_9_tax_payable": g3["summary"]["output_tax"],
            "documents_issued": g1.get("doc_issue") or g1.get("docs") or None,
            "table_17_hsn_outward": reports.hsn_summary(docs),
            "note": "Prepared from your books. Tables 8 (ITC as per 2B), 10–14 (amendments) and 15–16 need the portal's figures."}


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _quarters(fy: FinancialYear) -> list[tuple[str, date, date]]:
    out = []
    for q in range(4):
        start = _add_months(fy.start_date.replace(day=1), 3 * q)
        end = _add_months(start, 3) - timedelta(days=1)
        out.append((f"Q{q + 1}", start, min(end, fy.end_date)))
    return out


def cmp08(db: Session, access: BusinessAccess, *, fy_start: date, rate: Decimal) -> dict[str, Any]:
    """CMP-08 for a composition dealer, quarter by quarter: tax at the composition rate on outward
    turnover, plus tax on inward supplies under reverse charge. GSTR-4 is the year's total."""
    access.require("gst.view")
    fy = _fy_of(db, access.business.id, fy_start)
    quarters = []
    for name, start, end in _quarters(fy):
        sales = sum((reports._sign(d) * d.taxable_total for d in reports.documents(
            db, access.business.id, reports.SALES_TYPES, start, end, None)), ZERO)
        g3 = reports.gstr3b(db, access.business.id, start, end, None)
        rcm = g3["3.1"]["d_inward_reverse_charge"]
        inward_reg = inward_unreg = ZERO
        for d in reports.documents(db, access.business.id, reports.PURCHASE_TYPES, start, end, None):
            party = db.get(Party, d.party_id) if d.party_id else None
            registered = bool(party and party.gst_type in (PartyGstType.REGULAR, PartyGstType.COMPOSITION))
            amt = reports._sign(d) * d.taxable_total
            inward_reg, inward_unreg = (inward_reg + amt, inward_unreg) if registered else (inward_reg, inward_unreg + amt)
        tax = (sales * rate / 100).quantize(Decimal("0.01"))
        quarters.append({"quarter": name, "from": start, "to": end, "outward_turnover": sales,
                         "composition_tax": tax, "cgst": (tax / 2).quantize(Decimal("0.01")), "sgst": tax - (tax / 2).quantize(Decimal("0.01")),
                         "rcm_inward": rcm, "inward_registered": inward_reg, "inward_unregistered": inward_unreg,
                         "due_date": end + timedelta(days=18)})
    return {"financial_year": fy.code, "rate": rate, "quarters": quarters,
            "gstr4": {"outward_turnover": sum((q["outward_turnover"] for q in quarters), ZERO),
                      "composition_tax": sum((q["composition_tax"] for q in quarters), ZERO),
                      "inward_registered": sum((q["inward_registered"] for q in quarters), ZERO),
                      "inward_unregistered": sum((q["inward_unregistered"] for q in quarters), ZERO),
                      "due_date": date(fy.end_date.year, 6, 30)},
            "note": "Composition rates: 1% traders and manufacturers, 5% restaurants, 6% service providers."}


# ============================================================================ RCM self-invoices


def rcm_pending(db: Session, access: BusinessAccess) -> list[dict[str, Any]]:
    """Reverse-charge purchases from unregistered suppliers that still need a self-invoice."""
    access.require("gst.view")
    done = set(db.scalars(select(RcmSelfInvoice.document_id).where(RcmSelfInvoice.business_id == access.business.id)))
    out = []
    for d, p in db.execute(select(Document, Party).outerjoin(Party, Party.id == Document.party_id).where(
            Document.business_id == access.business.id, Document.doc_type == DocType.PURCHASE_INVOICE,
            Document.status == DocStatus.FINAL, Document.reverse_charge.is_(True)).order_by(Document.doc_date)).all():
        if p is not None and p.gstin:
            continue  # a registered supplier's own invoice serves
        si = None
        if d.id in done:
            si = db.scalar(select(RcmSelfInvoice).where(RcmSelfInvoice.document_id == d.id))
        out.append({"document_id": d.id, "number": d.number, "date": d.doc_date, "supplier": p.name if p else d.party_name,
                    "taxable": d.taxable_total, "tax": d.igst_total + d.cgst_total + d.sgst_total + d.cess_total,
                    "self_invoice": {"id": si.id, "number": si.number, "date": si.invoice_date} if si else None})
    return out


def create_self_invoice(db: Session, access: BusinessAccess, document_id: uuid.UUID, on: date) -> RcmSelfInvoice:
    access.require("gst.file")
    d = db.get(Document, document_id)
    if d is None or d.business_id != access.business.id or d.doc_type != DocType.PURCHASE_INVOICE or not d.reverse_charge:
        raise ValidationFailed("Choose a reverse-charge purchase bill")
    if db.scalar(select(RcmSelfInvoice.id).where(RcmSelfInvoice.document_id == d.id)):
        raise Conflict("A self-invoice already exists for this bill")
    fy = _fy_of(db, access.business.id, on)
    number = next_number(db, access.business.id, f"rcm_self/{fy.code}", prefix=f"RCM/{fy.code}/")
    si = RcmSelfInvoice(business_id=access.business.id, document_id=d.id, number=number, invoice_date=on,
                        created_by_id=access.user.id)
    db.add(si)
    db.flush()
    audit.record(db, action="gst.self_invoice", entity_type="document", entity_id=d.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"number": number})
    return si


def self_invoice_html(db: Session, access: BusinessAccess, si_id: uuid.UUID) -> str:
    access.require("gst.view")
    si = db.get(RcmSelfInvoice, si_id)
    if si is None or si.business_id != access.business.id:
        raise NotFound("Self-invoice not found")
    d = db.get(Document, si.document_id)
    assert d is not None
    b = access.business
    e = html.escape
    rows = "".join(
        f"<tr><td>{i}</td><td>{e(ln.description)}</td><td>{e(ln.hsn_sac or '')}</td><td class=n>{ln.quantity.normalize():f}</td>"
        f"<td class=n>{ln.taxable_value:.2f}</td><td class=n>{ln.gst_rate.normalize():f}%</td>"
        f"<td class=n>{(ln.igst_amount + ln.cgst_amount + ln.sgst_amount + ln.cess_amount):.2f}</td></tr>"
        for i, ln in enumerate(d.lines, 1))
    tax = d.igst_total + d.cgst_total + d.sgst_total + d.cess_total
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Self-invoice {e(si.number)}</title>
<style>body{{font:10pt Arial,"Noto Sans",sans-serif;margin:16mm}}table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #999;padding:4px}}
.n{{text-align:right}}h2{{margin:0}}</style></head><body>
<h2>Self-invoice (Reverse charge)</h2><p>Issued under section 31(3)(f) of the CGST Act for supplies received from an unregistered person.</p>
<p><b>No:</b> {e(si.number)} &nbsp; <b>Date:</b> {si.invoice_date:%d-%m-%Y}</p>
<p><b>Recipient (us):</b> {e(b.legal_name or b.name)} · GSTIN {e(b.gstin or '')}<br>
<b>Supplier (unregistered):</b> {e(d.party_name or '')} · purchase {e(d.number or '')} of {d.doc_date:%d-%m-%Y}<br>
<b>Place of supply:</b> {e(d.place_of_supply or '')} · <b>Tax payable on reverse charge:</b> Yes</p>
<table><tr><th>#</th><th>Description</th><th>HSN/SAC</th><th>Qty</th><th>Taxable</th><th>Rate</th><th>Tax</th></tr>{rows}
<tr><td colspan=4><b>Total</b></td><td class=n><b>{d.taxable_total:.2f}</b></td><td></td><td class=n><b>{tax:.2f}</b></td></tr></table>
<p><b>Tax payable by us under reverse charge:</b> {rupees_in_words(tax)}</p>
<p style="margin-top:20mm">For {e(b.legal_name or b.name)}<br><br>Authorised Signatory</p></body></html>"""


# ============================================================================ job work (ITC-04)


def _job_warehouse(db: Session, access: BusinessAccess, party: Party, branch_id: uuid.UUID) -> uuid.UUID:
    from app.modules.inventory.models import Warehouse

    code = f"JW-{str(party.id)[:6].upper()}"
    w = db.scalar(select(Warehouse).where(Warehouse.business_id == access.business.id, Warehouse.code == code))
    if w is None:
        w = Warehouse(business_id=access.business.id, branch_id=branch_id, code=code,
                      name=f"At job worker: {party.name}"[:200], is_active=True)
        db.add(w)
        db.flush()
    return w.id


def create_job_challan(db: Session, access: BusinessAccess, data: dict[str, Any]) -> JobWorkChallan:
    from app.modules.catalog.models import Product
    from app.modules.inventory import service as inventory
    from app.modules.inventory.models import Warehouse

    access.require("stock.edit")
    party = db.get(Party, data["party_id"])
    if party is None or party.business_id != access.business.id:
        raise ValidationFailed("Job worker not found")
    wh = db.get(Warehouse, data["warehouse_id"])
    if wh is None or wh.business_id != access.business.id:
        raise ValidationFailed("Godown not found")
    direction = JobWorkDirection(data["direction"])
    sent: JobWorkChallan | None = None
    if direction == JobWorkDirection.IN:
        if not data.get("sent_challan_id"):
            raise ValidationFailed("Choose the challan the goods were sent on")
        sent = db.get(JobWorkChallan, data["sent_challan_id"])
        if sent is None or sent.business_id != access.business.id or sent.direction != JobWorkDirection.OUT or sent.party_id != party.id:
            raise ValidationFailed("Sent challan not found for this job worker")
    jw = _job_warehouse(db, access, party, wh.branch_id)
    lines, stock_lines = [], []
    for i, ln in enumerate(data["lines"], 1):
        p = db.get(Product, ln["product_id"])
        if p is None or p.business_id != access.business.id:
            raise ValidationFailed(f"Line {i}: product not found")
        qty = Decimal(ln["quantity"])
        value = Decimal(ln.get("value") or (qty * (p.purchase_price or p.selling_price or ZERO))).quantize(Decimal("0.01"))
        lines.append({"product_id": str(p.id), "name": p.name, "hsn": p.hsn_sac, "quantity": str(qty), "value": str(value),
                      "batch_no": ln.get("batch_no")})
        stock_lines.append(inventory.LineIn(product_id=p.id, quantity=qty, batch_no=ln.get("batch_no")))
    fy = _fy_of(db, access.business.id, data["challan_date"])
    prefix = "JWO" if direction == JobWorkDirection.OUT else "JWI"
    number = next_number(db, access.business.id, f"job_work_{direction.value}/{fy.code}", prefix=f"{prefix}/{fy.code}/")
    src, dst = (wh.id, jw) if direction == JobWorkDirection.OUT else (jw, wh.id)
    doc = inventory.create_transfer(db, access, from_warehouse_id=src, to_warehouse_id=dst, doc_date=data["challan_date"],
                                    lines=stock_lines, reference=number, notes=f"Job work: {data.get('process') or ''}".strip(": "))
    c = JobWorkChallan(business_id=access.business.id, number=number, direction=direction,
                       nature=JobWorkNature(data.get("nature") or (sent.nature if sent else "inputs")), party_id=party.id,
                       challan_date=data["challan_date"], warehouse_id=wh.id, job_warehouse_id=jw, stock_document_id=doc.id,
                       sent_challan_id=sent.id if sent else None, lines=lines, process=data.get("process"),
                       notes=data.get("notes"), created_by_id=access.user.id)
    db.add(c)
    db.flush()
    return c


def job_challans(db: Session, access: BusinessAccess) -> list[JobWorkChallan]:
    access.require("stock.view")
    return list(db.scalars(select(JobWorkChallan).where(JobWorkChallan.business_id == access.business.id)
                           .order_by(JobWorkChallan.challan_date.desc(), JobWorkChallan.number.desc()).limit(500)))


def itc04(db: Session, access: BusinessAccess, *, date_from: date, date_to: date, today: date) -> dict[str, Any]:
    """ITC-04: goods sent to and received back from job workers in the period, and what is still
    with them — inputs over 1 year / capital goods over 3 years become a deemed supply."""
    access.require("stock.view")
    rows = list(db.scalars(select(JobWorkChallan).where(JobWorkChallan.business_id == access.business.id)
                           .order_by(JobWorkChallan.challan_date)))
    parties = {p.id: p for p in db.scalars(select(Party).where(Party.id.in_({r.party_id for r in rows})))} if rows else {}
    sent: list[dict[str, Any]] = []
    received: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    back: dict[uuid.UUID, dict[str, Decimal]] = {}
    for r in rows:
        if r.direction == JobWorkDirection.IN and r.sent_challan_id:
            got = back.setdefault(r.sent_challan_id, {})
            for ln in r.lines:
                got[ln["product_id"]] = got.get(ln["product_id"], ZERO) + Decimal(ln["quantity"])
    for r in rows:
        p = parties.get(r.party_id)
        base = {"number": r.number, "date": r.challan_date, "job_worker": p.name if p else None,
                "gstin": p.gstin if p else None, "state": p.state_code if p else None, "nature": r.nature.value,
                "lines": r.lines}
        if date_from <= r.challan_date <= date_to:
            (sent if r.direction == JobWorkDirection.OUT else received).append(base)
        if r.direction == JobWorkDirection.OUT:
            got = back.get(r.id, {})
            left = [{**ln, "pending": str((Decimal(ln["quantity"]) - got.get(ln["product_id"], ZERO)).quantize(Decimal("0.001")))} for ln in r.lines
                    if Decimal(ln["quantity"]) - got.get(ln["product_id"], ZERO) > 0]
            if left:
                limit = r.challan_date + timedelta(days=365 * (3 if r.nature == JobWorkNature.CAPITAL_GOODS else 1))
                pending.append({**base, "lines": left, "return_by": limit, "overdue": today > limit})
    return {"date_from": date_from, "date_to": date_to, "sent": sent, "received": received, "pending": pending}


# ============================================================================ TDS 194Q


def tds_194q(db: Session, access: BusinessAccess, *, fy_start: date, threshold: Decimal = Decimal(5_000_000)) -> dict[str, Any]:
    """Purchases per supplier in the financial year against the ₹50 lakh limit of section 194Q
    (applies when your turnover last year was over ₹10 crore): TDS at 0.1% on the excess."""
    access.require("purchases.view")
    fy = _fy_of(db, access.business.id, fy_start)
    rows: list[dict[str, Any]] = []
    for pid, name, pan, raw_total in db.execute(
            select(Party.id, Party.name, Party.pan, func.sum(Document.taxable_total)).join(Party, Party.id == Document.party_id)
            .where(Document.business_id == access.business.id, Document.doc_type == DocType.PURCHASE_INVOICE,
                   Document.status == DocStatus.FINAL, Document.doc_date >= fy.start_date, Document.doc_date <= fy.end_date)
            .group_by(Party.id, Party.name, Party.pan)).all():
        total = Decimal(raw_total or 0)
        excess = max(ZERO, total - threshold)
        rows.append({"party_id": pid, "name": name, "pan": pan, "purchases": total, "excess": excess,
                     "tds": (excess * Decimal("0.001")).quantize(Decimal("0.01")),
                     "near": total >= threshold * Decimal("0.8") and total < threshold})
    rows.sort(key=lambda r: Decimal(str(r["purchases"])), reverse=True)
    return {"financial_year": fy.code, "threshold": threshold, "rows": rows,
            "note": "Without the supplier's PAN the rate is 5%. Record the deduction on the payment with section 194Q."}


def form_26q_rows(db: Session, access: BusinessAccess, *, date_from: date, date_to: date) -> list[list[str]]:
    """Deductee details for the quarterly TDS return (non-salary), for your accountant or the RPU."""
    from app.modules.billing.models import Payment, PaymentDirection, PaymentState

    access.require("payments.view")
    out = [["Deductee name", "PAN", "Section", "Payment date", "Amount paid/credited", "TDS deducted", "Rate %", "Voucher"]]
    for pay, party in db.execute(select(Payment, Party).outerjoin(Party, Party.id == Payment.party_id).where(
            Payment.business_id == access.business.id, Payment.direction == PaymentDirection.OUT, Payment.tds_amount > 0,
            Payment.status == PaymentState.POSTED, Payment.payment_date >= date_from, Payment.payment_date <= date_to)
            .order_by(Payment.payment_date)).all():
        gross = pay.amount + pay.tds_amount
        out.append([party.name if party else "", (party.pan or "PANNOTAVBL") if party else "PANNOTAVBL", pay.tds_section or "",
                    pay.payment_date.strftime("%d/%m/%Y"), f"{gross:.2f}", f"{pay.tds_amount:.2f}",
                    f"{(pay.tds_amount * 100 / gross):.2f}" if gross else "0", pay.number])
    return out
