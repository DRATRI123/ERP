"""Sale-or-return: goods sent on a delivery challan stay the customer's to sell or send back.

For each challan line: sent, invoiced (sales invoices made from the challan), returned unsold, and
still with the customer. Returning unsold goods brings them back into stock at the cost they left at.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document, DocumentLine
from app.modules.inventory import service as inventory
from app.modules.inventory.models import AdjustmentReason, StockLedgerEntry
from app.modules.stockx.models import ChallanReturn

ZERO = Decimal(0)


def _invoiced(db: Session, line_ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    if not line_ids:
        return {}
    rows = db.execute(select(DocumentLine.source_line_id, func.sum(DocumentLine.quantity))
                      .join(Document, Document.id == DocumentLine.document_id)
                      .where(DocumentLine.source_line_id.in_(line_ids), Document.doc_type == DocType.SALES_INVOICE,
                             Document.status == DocStatus.FINAL).group_by(DocumentLine.source_line_id)).all()
    return {k: Decimal(v) for k, v in rows if k is not None}


def _returned(db: Session, line_ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    if not line_ids:
        return {}
    rows = db.execute(select(ChallanReturn.line_id, func.sum(ChallanReturn.quantity))
                      .where(ChallanReturn.line_id.in_(line_ids)).group_by(ChallanReturn.line_id)).all()
    return {k: Decimal(v) for k, v in rows}


def _lines(db: Session, docs: list[Document]) -> list[dict[str, Any]]:
    ids = [ln.id for d in docs for ln in d.lines if ln.product_id]
    inv, ret = _invoiced(db, ids), _returned(db, ids)
    out = []
    for d in docs:
        for ln in d.lines:
            if not ln.product_id:
                continue
            sold, back = inv.get(ln.id, ZERO), ret.get(ln.id, ZERO)
            out.append({"document_id": d.id, "number": d.number, "doc_date": d.doc_date, "party_id": d.party_id,
                        "party_name": d.party_name, "line_id": ln.id, "product_id": ln.product_id,
                        "description": ln.description, "unit_price": ln.unit_price, "sent": ln.quantity,
                        "invoiced": sold, "returned": back, "pending": max(ln.quantity - sold - back, ZERO),
                        "pending_value": max(ln.quantity - sold - back, ZERO) * ln.unit_price})
    return out


def report(db: Session, access: BusinessAccess, *, party_id: uuid.UUID | None, open_only: bool) -> dict[str, Any]:
    access.require("invoices.view")
    stmt = select(Document).where(Document.business_id == access.business.id,
                                  Document.doc_type == DocType.DELIVERY_CHALLAN, Document.status == DocStatus.FINAL)
    if party_id:
        stmt = stmt.where(Document.party_id == party_id)
    rows = _lines(db, list(db.scalars(stmt.order_by(Document.doc_date.desc()).limit(1000))))
    if open_only:
        rows = [r for r in rows if r["pending"] > 0]
    parties: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = parties.setdefault(r["party_name"] or "—", {"party_name": r["party_name"] or "—", "pending_value": ZERO, "lines": 0})
        p["pending_value"] += r["pending_value"]
        p["lines"] += 1 if r["pending"] > 0 else 0
    return {"lines": rows, "parties": sorted(parties.values(), key=lambda p: -p["pending_value"]),
            "pending_value": sum((r["pending_value"] for r in rows), ZERO)}


def return_unsold(db: Session, access: BusinessAccess, document_id: uuid.UUID, *, on: date,
                  lines: list[dict[str, Any]]) -> list[ChallanReturn]:
    """``lines``: [{line_id, quantity}]. Each goes back into the challan's warehouse at the cost it left at."""
    access.require("stock.edit")
    doc = db.get(Document, document_id)
    if doc is None or doc.business_id != access.business.id or doc.doc_type != DocType.DELIVERY_CHALLAN:
        raise NotFound("Delivery challan not found")
    if doc.status != DocStatus.FINAL:
        raise ValidationFailed("Only a final delivery challan can have goods returned")
    if on < doc.doc_date:
        raise ValidationFailed("Return date cannot be before the challan date")
    state = {r["line_id"]: r for r in _lines(db, [doc])}
    by_line = {ln.id: ln for ln in doc.lines}
    stock_lines, todo = [], []
    for i, item in enumerate(lines, 1):
        lid = uuid.UUID(str(item["line_id"]))
        qty = Decimal(str(item["quantity"]))
        if qty <= 0:
            continue
        if lid not in state:
            raise ValidationFailed(f"Line {i} is not on this challan")
        if qty > state[lid]["pending"]:
            raise ValidationFailed(f"{state[lid]['description']}: only {state[lid]['pending']} still with the customer")
        ln = by_line[lid]
        assert ln.product_id is not None  # only product lines are listed
        cost = db.scalar(select(StockLedgerEntry.unit_cost).where(
            StockLedgerEntry.source_id == doc.id, StockLedgerEntry.source_line_id == lid).limit(1))
        stock_lines.append(inventory.LineIn(product_id=ln.product_id, quantity=qty, unit_cost=cost, batch_no=ln.batch_no,
                                            expiry_date=ln.expiry_date, serials=list(ln.serials or [])))
        todo.append((lid, qty))
    if not todo:
        raise ValidationFailed("Enter the quantity that came back")
    if doc.warehouse_id is None:
        raise ValidationFailed("This challan has no warehouse")
    sd = inventory.create_adjustment(db, access, warehouse_id=doc.warehouse_id, doc_date=on,
                                     reason=AdjustmentReason.CORRECTION, lines=stock_lines,
                                     reference=f"Return {doc.number}"[:60], notes=f"Unsold goods returned on challan {doc.number}")
    out = []
    for lid, qty in todo:
        r = ChallanReturn(business_id=access.business.id, document_id=doc.id, line_id=lid, return_date=on, quantity=qty,
                          stock_document_id=sd.id, created_by_id=access.user.id)
        db.add(r)
        out.append(r)
    db.flush()
    audit.record(db, action="challan.goods_returned", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"lines": [{"line_id": str(lid), "quantity": q} for lid, q in todo]})
    return out

