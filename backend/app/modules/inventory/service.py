"""Warehouses and inventory documents (opening stock, adjustments, transfers, physical counts)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.businesses.models import Branch, FinancialYear
from app.modules.catalog.models import Product
from app.modules.events import service as events
from app.modules.inventory.engine import ZERO, Movement, PostedMovement, StockEngine, q4
from app.modules.inventory.models import (
    AdjustmentReason,
    Batch,
    StockBalance,
    StockDocument,
    StockDocumentKind,
    StockDocumentLine,
    StockDocumentStatus,
    StockLedgerEntry,
    Warehouse,
)
from app.modules.numbering.service import next_number

SOURCE = "stock_document"
_PREFIX = {
    StockDocumentKind.OPENING: "OS",
    StockDocumentKind.ADJUSTMENT: "ADJ",
    StockDocumentKind.TRANSFER: "TRF",
    StockDocumentKind.COUNT: "PC",
    StockDocumentKind.PRODUCTION: "MFG",
}

# ---------------------------------------------------------------------------- warehouses


def visible_warehouse_ids(db: Session, access: BusinessAccess) -> list[uuid.UUID]:
    stmt = select(Warehouse.id).where(Warehouse.business_id == access.business.id)
    if not access.all_branches:
        stmt = stmt.where(Warehouse.branch_id.in_(access.branch_ids))
    return list(db.scalars(stmt))


def list_warehouses(db: Session, access: BusinessAccess, *, include_inactive: bool) -> list[Warehouse]:
    stmt = select(Warehouse).where(Warehouse.business_id == access.business.id).order_by(Warehouse.code)
    if not include_inactive:
        stmt = stmt.where(Warehouse.is_active.is_(True))
    if not access.all_branches:
        stmt = stmt.where(Warehouse.branch_id.in_(access.branch_ids))
    return list(db.scalars(stmt))


def get_warehouse(db: Session, access: BusinessAccess, warehouse_id: uuid.UUID) -> Warehouse:
    w = db.get(Warehouse, warehouse_id)
    if w is None or w.business_id != access.business.id or not access.can_access_branch(w.branch_id):
        raise NotFound("Warehouse not found")
    return w


def _check_branch(db: Session, access: BusinessAccess, branch_id: uuid.UUID) -> None:
    b = db.get(Branch, branch_id)
    if b is None or b.business_id != access.business.id or not access.can_access_branch(b.id):
        raise ValidationFailed("Branch not found in this business")


def _code_free(db: Session, business_id: uuid.UUID, code: str, exclude: uuid.UUID | None) -> None:
    stmt = select(Warehouse.id).where(Warehouse.business_id == business_id, Warehouse.code == code)
    if exclude:
        stmt = stmt.where(Warehouse.id != exclude)
    if db.scalar(stmt):
        raise Conflict(f"Warehouse code {code} is already in use")


def create_warehouse(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Warehouse:
    access.require("warehouses.manage")
    _check_branch(db, access, data["branch_id"])
    _code_free(db, access.business.id, data["code"], None)
    w = Warehouse(business_id=access.business.id, **data)
    db.add(w)
    db.flush()
    audit.record(db, action="warehouse.created", entity_type="warehouse", entity_id=w.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"code": w.code, "name": w.name, "branch_id": w.branch_id})
    return w


def update_warehouse(db: Session, access: BusinessAccess, w: Warehouse, data: dict[str, Any]) -> Warehouse:
    access.require("warehouses.manage")
    if "branch_id" in data:
        _check_branch(db, access, data["branch_id"])
    if "code" in data:
        _code_free(db, access.business.id, data["code"], w.id)
    if data.get("is_active") is False:
        held = db.scalar(select(StockBalance.id).where(
            StockBalance.warehouse_id == w.id, StockBalance.quantity > 0).limit(1))
        if held:
            raise ValidationFailed("Move or adjust out the stock before deactivating this warehouse")
    fields = ["code", "name", "branch_id", "address", "is_active"]
    before = audit.snapshot(w, fields)
    for k, v in data.items():
        setattr(w, k, v)
    audit.record(db, action="warehouse.updated", entity_type="warehouse", entity_id=w.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes=audit.diff(before, audit.snapshot(w, fields)))
    return w


def create_default_warehouse(db: Session, business_id: uuid.UUID, branch_id: uuid.UUID) -> Warehouse:
    w = Warehouse(business_id=business_id, branch_id=branch_id, code="MAIN", name="Main Store")
    db.add(w)
    return w


# ---------------------------------------------------------------------------- inputs


@dataclass
class LineIn:
    product_id: uuid.UUID
    quantity: Decimal = ZERO
    unit_cost: Decimal | None = None
    batch_no: str | None = None
    mfg_date: date | None = None
    expiry_date: date | None = None
    serials: list[str] = field(default_factory=list)
    counted_quantity: Decimal | None = None


def _open_fy(db: Session, business_id: uuid.UUID, d: date) -> FinancialYear:
    fy = db.scalar(select(FinancialYear).where(
        FinancialYear.business_id == business_id,
        FinancialYear.start_date <= d, FinancialYear.end_date >= d))
    if fy is None:
        raise ValidationFailed(f"No financial year covers {d.isoformat()}; create it first")
    if fy.is_closed:
        raise ValidationFailed(f"Financial year {fy.code} is closed")
    return fy


def _active_warehouse(db: Session, access: BusinessAccess, wid: uuid.UUID) -> Warehouse:
    w = get_warehouse(db, access, wid)
    if not w.is_active:
        raise ValidationFailed(f"Warehouse {w.code} is inactive")
    return w


def _product(db: Session, access: BusinessAccess, pid: uuid.UUID) -> Product:
    p = db.get(Product, pid)
    if p is None or p.business_id != access.business.id:
        raise ValidationFailed("Product not found in this business", details={"product_id": str(pid)})
    if not p.is_active:
        raise ValidationFailed(f"{p.name} is archived")
    return p


def _batch(db: Session, access: BusinessAccess, p: Product, line: LineIn, *, create: bool) -> Batch | None:
    if not p.batch_tracking:
        if line.batch_no:
            raise ValidationFailed(f"{p.name} is not batch-tracked")
        return None
    if not line.batch_no:
        raise ValidationFailed(f"{p.name} is batch-tracked: batch_no is required")
    b = db.scalar(select(Batch).where(Batch.product_id == p.id, Batch.batch_no == line.batch_no))
    if b is None:
        if not create:
            raise ValidationFailed(f"Batch {line.batch_no} of {p.name} does not exist")
        if line.expiry_date and line.mfg_date and line.expiry_date <= line.mfg_date:
            raise ValidationFailed("Expiry date must be after manufacturing date")
        b = Batch(business_id=access.business.id, product_id=p.id, batch_no=line.batch_no,
                  mfg_date=line.mfg_date, expiry_date=line.expiry_date)
        db.add(b)
        db.flush()
    elif line.expiry_date and b.expiry_date and line.expiry_date != b.expiry_date:
        raise ValidationFailed(f"Batch {b.batch_no} already exists with expiry {b.expiry_date}")
    return b


def _default_cost(engine: StockEngine, p: Product, wid: uuid.UUID, batch: Batch | None) -> Decimal:
    """Cost for stock found/added without a stated cost: current average, else purchase price."""
    bal = engine.lock_balance(p.id, wid, batch.id if batch else None)
    if bal.quantity > 0:
        return q4(bal.value / bal.quantity)
    if p.purchase_price is not None:
        return p.purchase_price
    raise ValidationFailed(f"{p.name}: give a unit cost (no stock or purchase price to value it at)")


# ---------------------------------------------------------------------------- documents


def _new_document(db: Session, access: BusinessAccess, kind: StockDocumentKind, *, doc_date: date,
                  warehouse: Warehouse, to_warehouse: Warehouse | None = None,
                  reason: AdjustmentReason | None = None, reference: str | None,
                  notes: str | None) -> StockDocument:
    fy = _open_fy(db, access.business.id, doc_date)
    number = next_number(db, access.business.id, f"stock.{kind.value}/{fy.code}",
                         prefix=f"{_PREFIX[kind]}/{fy.code}/")
    doc = StockDocument(
        business_id=access.business.id, financial_year_id=fy.id, kind=kind, number=number,
        doc_date=doc_date, warehouse_id=warehouse.id,
        to_warehouse_id=to_warehouse.id if to_warehouse else None, reason=reason,
        reference=reference, notes=notes, created_by_id=access.user.id,
    )
    db.add(doc)
    db.flush()
    return doc


def _finish(db: Session, access: BusinessAccess, doc: StockDocument, engine: StockEngine,
            movements: list[Movement]) -> StockDocument:
    if not movements:
        raise ValidationFailed("The document changes no stock")
    before = {m.product.id: engine.on_hand(m.product.id) for m in movements}
    posted = engine.post(movements, entry_date=doc.doc_date, source_type=SOURCE, source_id=doc.id)
    from app.modules.accounting.posting import post_stock_document

    post_stock_document(db, access.business, doc, user_id=access.user.id)
    _emit(db, access, doc, posted, before, engine)
    audit.record(db, action=f"stock.{doc.kind.value}_posted", entity_type="stock_document",
                 entity_id=doc.id, business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": doc.number, "lines": len(doc.lines)})
    return doc


def _emit(db: Session, access: BusinessAccess, doc: StockDocument, posted: list[PostedMovement],
          before: dict[uuid.UUID, Decimal], engine: StockEngine) -> None:
    changes = [{"product_id": p.entry.product_id, "warehouse_id": p.entry.warehouse_id,
                "batch_id": p.entry.batch_id, "quantity": p.entry.quantity,
                "balance_quantity": p.entry.balance_quantity} for p in posted]
    wh = db.get(Warehouse, doc.warehouse_id)
    base = {"document_id": doc.id, "number": doc.number, "kind": doc.kind.value,
            "branch_id": wh.branch_id if wh else None,
            "status": doc.status.value, "changes": changes}
    events.emit(db, "stock.updated", aggregate_type="stock_document", aggregate_id=doc.id,
                business_id=access.business.id, payload=base)
    if doc.kind == StockDocumentKind.TRANSFER and doc.status == StockDocumentStatus.POSTED:
        events.emit(db, "stock.transferred", aggregate_type="stock_document",
                    aggregate_id=doc.id, business_id=access.business.id,
                    payload={**base, "from_warehouse_id": doc.warehouse_id,
                             "to_warehouse_id": doc.to_warehouse_id})
    for pid in {p.entry.product_id for p in posted}:
        product = db.get(Product, pid)
        if product is None or product.reorder_level is None:
            continue
        after = engine.on_hand(pid)
        if before[pid] > product.reorder_level >= after:
            events.emit(db, "stock.low", aggregate_type="product", aggregate_id=pid,
                        business_id=access.business.id,
                        payload={"product_id": pid, "sku": product.sku, "name": product.name,
                                 "on_hand": after, "reorder_level": product.reorder_level})


def _add_line(doc: StockDocument, line_no: int, p: Product, batch: Batch | None, qty: Decimal,
              unit_cost: Decimal | None, serials: list[str], **extra: Any) -> StockDocumentLine:
    ln = StockDocumentLine(line_no=line_no, product_id=p.id, batch_id=batch.id if batch else None,
                           quantity=qty, unit_cost=unit_cost, serials=serials or None, **extra)
    doc.lines.append(ln)
    return ln


def create_opening(db: Session, access: BusinessAccess, *, warehouse_id: uuid.UUID, doc_date: date,
                   lines: list[LineIn], reference: str | None, notes: str | None) -> StockDocument:
    access.require("stock.edit")
    wh = _active_warehouse(db, access, warehouse_id)
    doc = _new_document(db, access, StockDocumentKind.OPENING, doc_date=doc_date, warehouse=wh,
                        reference=reference, notes=notes)
    engine = StockEngine(db, access.business)
    movements = []
    for i, line in enumerate(lines, 1):
        p = _product(db, access, line.product_id)
        if line.quantity <= 0:
            raise ValidationFailed(f"Line {i}: opening quantity must be positive")
        if line.unit_cost is None:
            raise ValidationFailed(f"Line {i}: unit cost is required for opening stock")
        batch = _batch(db, access, p, line, create=True)
        ln = _add_line(doc, i, p, batch, line.quantity, line.unit_cost, line.serials)
        db.flush()
        movements.append(Movement(p, wh.id, line.quantity, batch.id if batch else None,
                                  unit_cost=line.unit_cost, serials=line.serials, line_id=ln.id))
    return _finish(db, access, doc, engine, movements)


def create_adjustment(db: Session, access: BusinessAccess, *, warehouse_id: uuid.UUID,
                      doc_date: date, reason: AdjustmentReason, lines: list[LineIn],
                      reference: str | None, notes: str | None) -> StockDocument:
    access.require("stock.edit")
    wh = _active_warehouse(db, access, warehouse_id)
    doc = _new_document(db, access, StockDocumentKind.ADJUSTMENT, doc_date=doc_date, warehouse=wh,
                        reason=reason, reference=reference, notes=notes)
    engine = StockEngine(db, access.business)
    movements = []
    for i, line in enumerate(lines, 1):
        p = _product(db, access, line.product_id)
        if line.quantity == 0:
            raise ValidationFailed(f"Line {i}: quantity cannot be zero")
        batch = _batch(db, access, p, line, create=line.quantity > 0)
        cost = line.unit_cost
        if line.quantity > 0 and cost is None:
            cost = _default_cost(engine, p, wh.id, batch)
        ln = _add_line(doc, i, p, batch, line.quantity, cost if line.quantity > 0 else None,
                       line.serials)
        db.flush()
        movements.append(Movement(p, wh.id, line.quantity, batch.id if batch else None,
                                  unit_cost=cost, serials=line.serials, line_id=ln.id))
    return _finish(db, access, doc, engine, movements)


def create_transfer(db: Session, access: BusinessAccess, *, from_warehouse_id: uuid.UUID,
                    to_warehouse_id: uuid.UUID, doc_date: date, lines: list[LineIn],
                    reference: str | None, notes: str | None) -> StockDocument:
    access.require("stock.edit")
    src = _active_warehouse(db, access, from_warehouse_id)
    dst = _active_warehouse(db, access, to_warehouse_id)
    if src.id == dst.id:
        raise ValidationFailed("Source and destination warehouses must differ")
    doc = _new_document(db, access, StockDocumentKind.TRANSFER, doc_date=doc_date, warehouse=src,
                        to_warehouse=dst, reference=reference, notes=notes)
    engine = StockEngine(db, access.business)
    movements: list[Movement] = []
    for i, line in enumerate(lines, 1):
        p = _product(db, access, line.product_id)
        if line.quantity <= 0:
            raise ValidationFailed(f"Line {i}: transfer quantity must be positive")
        batch = _batch(db, access, p, line, create=False)
        ln = _add_line(doc, i, p, batch, line.quantity, None, line.serials)
        db.flush()
        bid = batch.id if batch else None
        movements.append(Movement(p, src.id, -line.quantity, bid, serials=line.serials, line_id=ln.id))
        movements.append(Movement(p, dst.id, line.quantity, bid, serials=line.serials,
                                  line_id=ln.id, carry_from=len(movements) - 1))
    return _finish(db, access, doc, engine, movements)


def create_count(db: Session, access: BusinessAccess, *, warehouse_id: uuid.UUID, doc_date: date,
                 lines: list[LineIn], reference: str | None, notes: str | None) -> StockDocument:
    """Physical verification: record counted quantities and post the differences."""
    access.require("stock.edit")
    wh = _active_warehouse(db, access, warehouse_id)
    doc = _new_document(db, access, StockDocumentKind.COUNT, doc_date=doc_date, warehouse=wh,
                        reference=reference, notes=notes)
    engine = StockEngine(db, access.business)
    movements = []
    seen: set[tuple] = set()
    for i, line in enumerate(lines, 1):
        p = _product(db, access, line.product_id)
        if p.serial_tracking:
            raise ValidationFailed(f"{p.name} is serial-tracked; use an adjustment with serials")
        if line.counted_quantity is None or line.counted_quantity < 0:
            raise ValidationFailed(f"Line {i}: counted_quantity must be zero or more")
        batch = _batch(db, access, p, line, create=True)
        key = (p.id, batch.id if batch else None)
        if key in seen:
            raise ValidationFailed(f"Line {i}: {p.name} is counted twice")
        seen.add(key)
        bal = engine.lock_balance(p.id, wh.id, batch.id if batch else None)
        diff = line.counted_quantity - bal.quantity
        cost = None
        if diff > 0:
            cost = line.unit_cost if line.unit_cost is not None else _default_cost(engine, p, wh.id, batch)
        ln = _add_line(doc, i, p, batch, diff, cost, [], system_quantity=bal.quantity,
                       counted_quantity=line.counted_quantity)
        db.flush()
        if diff != 0:
            movements.append(Movement(p, wh.id, diff, batch.id if batch else None,
                                      unit_cost=cost, line_id=ln.id))
    if not movements:  # count matched the books: keep the record, post nothing
        audit.record(db, action="stock.count_posted", entity_type="stock_document",
                     entity_id=doc.id, business_id=access.business.id, actor_id=access.user.id,
                     changes={"number": doc.number, "lines": len(doc.lines), "differences": 0})
        return doc
    return _finish(db, access, doc, engine, movements)


def get_document(db: Session, access: BusinessAccess, doc_id: uuid.UUID) -> StockDocument:
    doc = db.get(StockDocument, doc_id)
    if doc is None or doc.business_id != access.business.id:
        raise NotFound("Stock document not found")
    visible = set(visible_warehouse_ids(db, access))
    if doc.warehouse_id not in visible and doc.to_warehouse_id not in visible:
        raise NotFound("Stock document not found")
    return doc


def document_entries(db: Session, doc: StockDocument) -> list[StockLedgerEntry]:
    return list(db.scalars(
        select(StockLedgerEntry)
        .where(StockLedgerEntry.source_type == SOURCE, StockLedgerEntry.source_id == doc.id)
        .order_by(StockLedgerEntry.posted_at, StockLedgerEntry.id)
    ))


def cancel_document(db: Session, access: BusinessAccess, doc: StockDocument, *, reason: str,
                    on: date) -> StockDocument:
    """Cancel by posting exact reversing entries (dated ``on``). Nothing is deleted."""
    access.require("stock.edit")
    if doc.status == StockDocumentStatus.CANCELLED:
        raise Conflict("This document is already cancelled")
    for wid in filter(None, (doc.warehouse_id, doc.to_warehouse_id)):
        get_warehouse(db, access, wid)  # must have access to every warehouse it touched
    _open_fy(db, access.business.id, on)
    originals = [e for e in document_entries(db, doc) if e.reverses_entry_id is None]
    engine = StockEngine(db, access.business)
    movements = [Movement(db.get(Product, e.product_id), e.warehouse_id, reverses=e,  # type: ignore[arg-type]
                          line_id=e.source_line_id)
                 for e in reversed(originals)]
    doc.status = StockDocumentStatus.CANCELLED
    doc.cancelled_at = datetime.now(UTC)
    doc.cancelled_by_id = access.user.id
    doc.cancel_reason = reason
    if movements:
        before = {m.product.id: engine.on_hand(m.product.id) for m in movements}
        posted = engine.post(movements, entry_date=on, source_type=SOURCE, source_id=doc.id)
        from app.modules.accounting.models import JournalSource
        from app.modules.accounting.posting import reverse as reverse_posting

        reverse_posting(db, access.business, JournalSource.STOCK, doc.id, on=on,
                        user_id=access.user.id)
        _emit(db, access, doc, posted, before, engine)
    audit.record(db, action="stock.document_cancelled", entity_type="stock_document",
                 entity_id=doc.id, business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": doc.number, "reason": reason})
    return doc


def has_stock_activity(db: Session, business_id: uuid.UUID) -> bool:
    return db.scalar(select(StockLedgerEntry.id).where(
        StockLedgerEntry.business_id == business_id).limit(1)) is not None

