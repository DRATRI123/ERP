"""Smart reordering: how much to buy, from whom, based on how fast things sell.

For each stocked product:

* **Daily sales rate** = quantity sold (sales invoices and delivery challans, net of returns)
  over the last ``days`` days ÷ ``days``.
* **On order** = quantity on purchase orders of the last 90 days not yet received on a
  purchase bill.
* **Suggested quantity** = rate × (supplier lead time + days of cover) − on hand − on order,
  and never less than what the product's own reorder level asks for. Rounded up to whole units.
* **Supplier and price** = from the most recent purchase bill of the product.

``draft_purchase_orders`` turns chosen suggestions into one draft purchase order per supplier.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document, DocumentLine
from app.modules.catalog.models import Product
from app.modules.inventory.models import StockBalance, StockLedgerEntry
from app.modules.inventory.service import visible_warehouse_ids
from app.modules.parties.models import Party

ZERO = Decimal(0)
SOLD = (DocType.SALES_INVOICE, DocType.DELIVERY_CHALLAN, DocType.CREDIT_NOTE)


def _ceil(d: Decimal) -> Decimal:
    return d.to_integral_value(rounding=ROUND_CEILING)


def suggestions(db: Session, access: BusinessAccess, *, days: int = 30, lead_days: int = 7, cover_days: int = 30,
                include_all: bool = False, today: date | None = None) -> list[dict[str, Any]]:
    access.require("stock.view")
    today = today or local_today()
    since = today - timedelta(days=days)
    wids = visible_warehouse_ids(db, access)
    bid = access.business.id

    on_hand = dict(db.execute(
        select(StockBalance.product_id, func.sum(StockBalance.quantity))
        .where(StockBalance.business_id == bid, StockBalance.warehouse_id.in_(wids))
        .group_by(StockBalance.product_id)).all())

    # Net quantity that left through sales (credit notes bring it back).
    sold = dict(db.execute(
        select(StockLedgerEntry.product_id, -func.sum(StockLedgerEntry.quantity))
        .join(Document, Document.id == StockLedgerEntry.source_id)
        .where(StockLedgerEntry.business_id == bid, StockLedgerEntry.warehouse_id.in_(wids),
               StockLedgerEntry.source_type == "billing_document", StockLedgerEntry.entry_date > since, StockLedgerEntry.entry_date <= today,
               Document.doc_type.in_(SOLD))
        .group_by(StockLedgerEntry.product_id)).all())

    # Open purchase orders: ordered minus already billed against them.
    po_lines = db.execute(
        select(DocumentLine.id, DocumentLine.product_id, DocumentLine.quantity).join(Document)
        .where(Document.business_id == bid, Document.doc_type == DocType.PURCHASE_ORDER,
               Document.status == DocStatus.FINAL, Document.doc_date >= today - timedelta(days=90),
               DocumentLine.product_id.is_not(None))).all()
    billed = dict(db.execute(
        select(DocumentLine.source_line_id, func.sum(DocumentLine.quantity)).join(Document)
        .where(Document.doc_type == DocType.PURCHASE_INVOICE, Document.status == DocStatus.FINAL,
               DocumentLine.source_line_id.in_([r[0] for r in po_lines] or [uuid.uuid4()]))
        .group_by(DocumentLine.source_line_id)).all())
    on_order: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for lid, pid, q in po_lines:
        on_order[pid] += max(q - billed.get(lid, ZERO), ZERO)

    products = db.scalars(select(Product).where(Product.business_id == bid, Product.is_active.is_(True),
                                                Product.track_inventory.is_(True)))
    out = []
    for p in products:
        have = on_hand.get(p.id, ZERO) or ZERO
        rate = (sold.get(p.id, ZERO) or ZERO) / days
        ordered = on_order.get(p.id, ZERO)
        need = rate * (lead_days + cover_days) - have - ordered
        if p.reorder_level is not None and have + ordered <= p.reorder_level:
            floor = p.reorder_quantity if p.reorder_quantity else p.reorder_level * 2 - have - ordered
            need = max(need, floor)
        qty = _ceil(max(need, ZERO))
        if qty <= 0 and not include_all:
            continue
        out.append({"product_id": p.id, "sku": p.sku, "name": p.name, "on_hand": have, "on_order": ordered,
                    "daily_sales": rate.quantize(Decimal("0.001")), "days_of_stock": int(have / rate) if rate > 0 else None,
                    "reorder_level": p.reorder_level, "suggested_quantity": qty, **_last_purchase(db, bid, p)})
    return sorted(out, key=lambda r: (r["days_of_stock"] is None, r["days_of_stock"] or 0, r["name"]))


def _last_purchase(db: Session, business_id: uuid.UUID, p: Product) -> dict[str, Any]:
    row = db.execute(
        select(Document.party_id, Document.party_name, DocumentLine.unit_price, Document.doc_date).join(Document)
        .where(Document.business_id == business_id, Document.doc_type == DocType.PURCHASE_INVOICE,
               Document.status == DocStatus.FINAL, DocumentLine.product_id == p.id)
        .order_by(Document.doc_date.desc(), Document.created_at.desc()).limit(1)).first()
    if row is None:
        return {"supplier_id": None, "supplier_name": None, "last_price": p.purchase_price, "last_purchase_date": None}
    return {"supplier_id": row[0], "supplier_name": row[1], "last_price": row[2], "last_purchase_date": row[3]}


def draft_purchase_orders(db: Session, access: BusinessAccess, items: list[dict[str, Any]]) -> list[Document]:
    """One draft purchase order per supplier for the chosen products and quantities."""
    from app.modules.billing.service import LineIn, create_draft

    groups: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        p = db.get(Product, it["product_id"])
        if p is None or p.business_id != access.business.id:
            raise ValidationFailed("Product not found")
        supplier = it.get("supplier_id") or _last_purchase(db, access.business.id, p)["supplier_id"]
        if supplier is None:
            raise ValidationFailed(f"Choose a supplier for {p.name} (it has never been bought)")
        s = db.get(Party, supplier)
        if s is None or s.business_id != access.business.id or not s.is_supplier:
            raise ValidationFailed("Supplier not found")
        groups[supplier].append({**it, "product": p})
    docs = []
    for supplier, rows in groups.items():
        lines = []
        for r in rows:
            price = r.get("unit_price") or _last_purchase(db, access.business.id, r["product"])["last_price"]
            lines.append(LineIn(product_id=r["product"].id, quantity=Decimal(r["quantity"]), unit_price=price))
        docs.append(create_draft(db, access, DocType.PURCHASE_ORDER,
                                 {"party_id": supplier, "doc_date": local_today(), "notes": "Suggested reorder"}, lines))
    return docs
