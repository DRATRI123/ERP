"""Batch and expiry control.

* **First-expiry-first-out**: a sales line for a batch-tracked product without a batch is
  given the batch that expires first (in the document's warehouse). When one batch does not
  have enough, the line is split into one line per batch, as pharmacy bills show it.
* **Expired stock cannot be sold** or delivered; it can still be returned to the supplier,
  adjusted or written off.
* **Per-batch MRP**: the MRP on a purchase line is kept on the batch and printed on sales of it.
* **Expiry alerts**: once a day, ``batch.expiring`` events at 90, 60 and 30 days before expiry
  and ``batch.expired`` on the day, each stage once per batch (automation rules react to them).
* **Expiry returns**: lists near-expiry/expired stock by the purchase bill it came from and
  drafts the purchase return (debit note) for it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.billing.doctypes import DocType, DocTypeSpec
from app.modules.billing.models import DocStatus, Document, DocumentLine
from app.modules.catalog.models import Product
from app.modules.events import service as events
from app.modules.inventory.engine import InsufficientStock
from app.modules.inventory.models import Batch, StockBalance, StockLedgerEntry, Warehouse

ZERO = Decimal(0)
ALERT_STAGES = (90, 60, 30, 0)
# Issue documents that may *not* take expired stock (returns to the supplier may).
SELLING = {DocType.SALES_INVOICE, DocType.DELIVERY_CHALLAN}


def _q2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def available_batches(db: Session, business_id: uuid.UUID, product_id: uuid.UUID, warehouse_id: uuid.UUID,
                      *, on: date) -> list[tuple[Batch, Decimal]]:
    """Unexpired batches with stock, first expiry first (batches without a date last)."""
    rows = db.execute(
        select(Batch, StockBalance.quantity).join(StockBalance, StockBalance.batch_id == Batch.id)
        .where(Batch.business_id == business_id, Batch.product_id == product_id,
               StockBalance.warehouse_id == warehouse_id, StockBalance.quantity > 0)
    ).all()
    usable = [(b, q) for b, q in rows if (b.expiry_date is None or b.expiry_date >= on) and not b.on_hold]
    return sorted(usable, key=lambda r: (r[0].expiry_date is None, r[0].expiry_date or date.max, r[0].batch_no))


def _copy_line(ln: DocumentLine, qty: Decimal, batch: Batch, discount_amount: Decimal) -> DocumentLine:
    skip = {"id", "document_id", "document", "line_no", "quantity", "batch_no", "expiry_date", "discount_amount"}
    cols = {c.key: getattr(ln, c.key) for c in DocumentLine.__mapper__.column_attrs if c.key not in skip}
    return DocumentLine(**cols, line_no=0, quantity=qty, batch_no=batch.batch_no, expiry_date=batch.expiry_date,
                        discount_amount=discount_amount)


def prepare_issue_lines(db: Session, access: BusinessAccess, doc: Document, spec: DocTypeSpec,
                        warehouse_id: uuid.UUID | None) -> None:
    """Before a stock-issuing document is finalized: pick batches first-expiry-first, refuse
    expired batches on sales, and carry each batch's MRP onto its line."""
    if spec.stock >= 0 or warehouse_id is None:
        return
    selling = doc.doc_type in SELLING
    claimed: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)  # batch id -> qty taken by earlier lines
    new_lines: list[DocumentLine] = []
    changed = False
    for ln in list(doc.lines):
        product = db.get(Product, ln.product_id) if ln.product_id else None
        if product is None or not product.batch_tracking or not product.track_inventory:
            new_lines.append(ln)
            continue
        if ln.source_line_id and doc.doc_type == DocType.SALES_INVOICE:
            src = db.get(DocumentLine, ln.source_line_id)
            src_doc = db.get(Document, src.document_id) if src else None
            if src_doc is not None and src_doc.doc_type == DocType.DELIVERY_CHALLAN:
                new_lines.append(ln)  # stock already left with the challan
                continue
        if ln.batch_no:
            b = db.scalar(select(Batch).where(Batch.product_id == product.id, Batch.batch_no == ln.batch_no))
            if b is not None:
                if selling and b.on_hold:
                    raise ValidationFailed(
                        f"Line {ln.line_no}: batch {b.batch_no} of {product.name} is on quality-control hold",
                        code="batch_on_hold")
                if selling and b.expiry_date is not None and b.expiry_date < doc.doc_date:
                    raise ValidationFailed(
                        f"Line {ln.line_no}: batch {b.batch_no} of {product.name} expired on "
                        f"{b.expiry_date:%d %b %Y} and cannot be sold", code="batch_expired")
                ln.expiry_date = b.expiry_date
                if b.mrp is not None and selling:
                    ln.mrp = b.mrp
                claimed[b.id] += ln.quantity
            new_lines.append(ln)
            continue
        # First expiry, first out.
        need = ln.quantity
        picks: list[tuple[Batch, Decimal]] = []
        for b, qty in available_batches(db, access.business.id, product.id, warehouse_id, on=doc.doc_date):
            free = qty - claimed[b.id]
            if free <= 0:
                continue
            take = min(free, need)
            picks.append((b, take))
            claimed[b.id] += take
            need -= take
            if need == 0:
                break
        if need > 0:
            have = ln.quantity - need
            raise InsufficientStock(
                f"Not enough unexpired stock of {product.name}: {have.normalize():f} available, "
                f"{ln.quantity.normalize():f} needed",
                details={"product_id": str(product.id), "available": str(have), "required": str(ln.quantity)})
        if len(picks) == 1:
            b = picks[0][0]
            ln.batch_no, ln.expiry_date = b.batch_no, b.expiry_date
            if b.mrp is not None:
                ln.mrp = b.mrp
            new_lines.append(ln)
            continue
        changed = True
        remaining_disc = ln.discount_amount
        for i, (b, take) in enumerate(picks):
            last = i == len(picks) - 1
            disc = remaining_disc if last else _q2(ln.discount_amount * take / ln.quantity)
            remaining_disc -= disc
            part = _copy_line(ln, take, b, disc)
            if b.mrp is not None:
                part.mrp = b.mrp
            new_lines.append(part)
        doc.lines.remove(ln)
    if changed:
        # Renumber in two steps so the (document, line_no) unique key never collides mid-flush.
        for i, ln in enumerate(new_lines, 1):
            ln.line_no = 10_000 + i
            if ln not in doc.lines:
                doc.lines.append(ln)
        db.flush()
        doc.lines.sort(key=new_lines.index)
        for i, ln in enumerate(new_lines, 1):
            ln.line_no = i
        db.flush()


# ---------------------------------------------------------------------------- alerts


def _stage(days_left: int) -> int | None:
    hits = [s for s in ALERT_STAGES if days_left <= s]
    return min(hits) if hits else None


def emit_expiry_alerts(db: Session, *, today: date | None = None) -> int:
    """Daily: announce batches in stock reaching 90/60/30 days to expiry, or expired."""
    today = today or local_today()
    horizon = date.fromordinal(today.toordinal() + max(ALERT_STAGES))
    rows = db.execute(
        select(Batch, Product, func.sum(StockBalance.quantity))
        .join(StockBalance, StockBalance.batch_id == Batch.id).join(Product, Product.id == Batch.product_id)
        .where(Batch.expiry_date.is_not(None), Batch.expiry_date <= horizon, StockBalance.quantity > 0)
        .group_by(Batch.id, Product.id)).all()
    n = 0
    for b, p, qty in rows:
        days_left = (b.expiry_date - today).days
        stage = _stage(days_left)
        if stage is None or (b.expiry_alert_stage is not None and stage >= b.expiry_alert_stage):
            continue
        b.expiry_alert_stage = stage
        events.emit(db, "batch.expired" if days_left < 0 or stage == 0 else "batch.expiring",
                    aggregate_type="batch", aggregate_id=b.id, business_id=b.business_id,
                    payload={"batch_id": b.id, "product_id": p.id, "sku": p.sku, "name": p.name,
                             "batch_no": b.batch_no, "expiry_date": b.expiry_date, "days_left": days_left,
                             "quantity": qty, "mrp": b.mrp})
        n += 1
    db.commit()
    return n


# ---------------------------------------------------------------------------- reports & returns


def _visible_warehouses(db: Session, access: BusinessAccess) -> list[uuid.UUID]:
    from app.modules.inventory.service import visible_warehouse_ids

    return list(visible_warehouse_ids(db, access))


def expiry_summary(db: Session, access: BusinessAccess, *, today: date | None = None) -> dict[str, Any]:
    """Counts and stock value expired / expiring within 30, 60 and 90 days."""
    today = today or local_today()
    wids = _visible_warehouses(db, access)
    rows = db.execute(
        select(Batch.expiry_date, StockBalance.quantity, StockBalance.value)
        .join(StockBalance, StockBalance.batch_id == Batch.id)
        .where(Batch.business_id == access.business.id, StockBalance.warehouse_id.in_(wids),
               StockBalance.quantity > 0, Batch.expiry_date.is_not(None))).all()
    buckets = {k: {"batches": 0, "quantity": ZERO, "value": ZERO} for k in ("expired", "d30", "d60", "d90")}
    for exp, qty, value in rows:
        days = (exp - today).days
        key = "expired" if days < 0 else "d30" if days <= 30 else "d60" if days <= 60 else "d90" if days <= 90 else None
        if key:
            buckets[key]["batches"] += 1
            buckets[key]["quantity"] += qty
            buckets[key]["value"] += value or ZERO
    return buckets


def _purchase_sources(db: Session, access: BusinessAccess, within_days: int, today: date) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Near-expiry/expired stock per purchase bill line it was bought on."""
    wids = _visible_warehouses(db, access)
    cutoff = date.fromordinal(today.toordinal() + within_days)
    stock = db.execute(
        select(Batch, StockBalance.warehouse_id, StockBalance.quantity)
        .join(StockBalance, StockBalance.batch_id == Batch.id)
        .where(Batch.business_id == access.business.id, StockBalance.warehouse_id.in_(wids),
               StockBalance.quantity > 0, Batch.expiry_date.is_not(None), Batch.expiry_date <= cutoff)).all()
    out: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for b, wid, qty in stock:
        receipt = db.execute(
            select(DocumentLine, Document)
            .join(StockLedgerEntry, StockLedgerEntry.source_line_id == DocumentLine.id)
            .join(Document, Document.id == DocumentLine.document_id)
            .where(StockLedgerEntry.batch_id == b.id, StockLedgerEntry.quantity > 0,
                   StockLedgerEntry.reverses_entry_id.is_(None), Document.doc_type == DocType.PURCHASE_INVOICE,
                   Document.status == DocStatus.FINAL)
            .order_by(Document.doc_date.desc())).first()
        if receipt is None:
            continue  # opening stock or a transfer: no bill to return against
        line, bill = receipt
        out[bill.id].append({"batch": b, "warehouse_id": wid, "quantity": qty, "line": line, "bill": bill})
    return out


def expiry_returns(db: Session, access: BusinessAccess, *, within_days: int = 0,
                   today: date | None = None) -> list[dict[str, Any]]:
    access.require("purchases.view")
    from app.modules.billing.service import _remaining_quantities

    today = today or local_today()
    result = []
    for bill_id, items in _purchase_sources(db, access, within_days, today).items():
        bill = items[0]["bill"]
        remaining = _remaining_quantities(db, bill, DocType.PURCHASE_RETURN)
        lines = []
        for it in items:
            ln = it["line"]
            qty = min(it["quantity"], remaining.get(ln.id, ln.quantity))
            if qty <= 0:
                continue
            lines.append({"product_id": ln.product_id, "description": ln.description, "batch_no": it["batch"].batch_no,
                          "expiry_date": it["batch"].expiry_date, "quantity": qty, "unit_price": ln.unit_price,
                          "source_line_id": ln.id, "warehouse_id": it["warehouse_id"],
                          "expired": it["batch"].expiry_date < today})
        if lines:
            result.append({"purchase_invoice_id": bill_id, "number": bill.number, "doc_date": bill.doc_date,
                           "supplier_id": bill.party_id, "supplier_name": bill.party_name, "lines": lines})
    return sorted(result, key=lambda r: (r["supplier_name"] or "", r["doc_date"]))


def draft_expiry_return(db: Session, access: BusinessAccess, purchase_invoice_id: uuid.UUID, *,
                        within_days: int = 0, today: date | None = None) -> Document:
    """A draft purchase return (debit note) for the near-expiry stock bought on one bill."""
    from app.modules.billing.service import LineIn, create_draft

    entry = next((r for r in expiry_returns(db, access, within_days=within_days, today=today)
                  if r["purchase_invoice_id"] == purchase_invoice_id), None)
    if entry is None:
        raise NotFound("No expiring stock to return on that purchase bill")
    bill = db.get(Document, purchase_invoice_id)
    by_wh: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for ln in entry["lines"]:
        by_wh[ln["warehouse_id"]].append(ln)
    wid, lines = max(by_wh.items(), key=lambda kv: len(kv[1]))  # one warehouse per document
    src = {ln.id: ln for ln in bill.lines}
    return create_draft(db, access, DocType.PURCHASE_RETURN, {
        "branch_id": db.get(Warehouse, wid).branch_id, "doc_date": today or local_today(),
        "party_id": bill.party_id, "reference_document_id": bill.id, "warehouse_id": wid,
        "place_of_supply": bill.place_of_supply, "notes": "Return of expired / near-expiry stock",
    }, [LineIn(product_id=ln["product_id"], quantity=ln["quantity"], unit_price=ln["unit_price"],
               discount_percent=src[ln["source_line_id"]].discount_percent,
               price_includes_tax=src[ln["source_line_id"]].price_includes_tax,
               gst_rate=src[ln["source_line_id"]].gst_rate, cess_rate=src[ln["source_line_id"]].cess_rate,
               batch_no=ln["batch_no"], expiry_date=ln["expiry_date"], source_line_id=ln["source_line_id"])
        for ln in lines])

