"""Read-only inventory reports. All are limited to warehouses the caller can access."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.access.service import BusinessAccess
from app.modules.catalog.models import Product
from app.modules.inventory.engine import ZERO, q4
from app.modules.inventory.models import (
    Batch,
    SerialNumber,
    SerialStatus,
    StockBalance,
    StockLedgerEntry,
    Warehouse,
)
from app.modules.inventory.service import visible_warehouse_ids


@dataclass
class BalanceRow:
    product_id: uuid.UUID
    sku: str
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    batch_id: uuid.UUID | None
    batch_no: str | None
    expiry_date: date | None
    quantity: Decimal
    value: Decimal
    average_cost: Decimal | None


def balances(db: Session, access: BusinessAccess, *, warehouse_id: uuid.UUID | None,
             product_id: uuid.UUID | None, q: str | None, include_zero: bool) -> Select:
    wids = visible_warehouse_ids(db, access)
    stmt = (
        select(StockBalance, Product, Warehouse, Batch)
        .join(Product, Product.id == StockBalance.product_id)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .outerjoin(Batch, Batch.id == StockBalance.batch_id)
        .where(StockBalance.business_id == access.business.id, StockBalance.warehouse_id.in_(wids))
        .order_by(Product.name, Warehouse.code, Batch.expiry_date, Batch.batch_no)
    )
    if not include_zero:
        stmt = stmt.where(StockBalance.quantity > 0)
    if warehouse_id:
        stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
    if product_id:
        stmt = stmt.where(StockBalance.product_id == product_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like),
                              Product.barcode == q))
    return stmt


def to_balance_row(bal: StockBalance, p: Product, w: Warehouse, b: Batch | None) -> BalanceRow:
    return BalanceRow(
        product_id=p.id, sku=p.sku, product_name=p.name, warehouse_id=w.id,
        warehouse_code=w.code, batch_id=b.id if b else None, batch_no=b.batch_no if b else None,
        expiry_date=b.expiry_date if b else None, quantity=bal.quantity, value=bal.value,
        average_cost=q4(bal.value / bal.quantity) if bal.quantity else None,
    )


@dataclass
class StockLevel:
    product_id: uuid.UUID
    sku: str
    name: str
    on_hand: Decimal
    value: Decimal
    reorder_level: Decimal | None
    reorder_quantity: Decimal | None


def low_stock(db: Session, access: BusinessAccess, *, warehouse_id: uuid.UUID | None) -> list[StockLevel]:
    """Active products with a reorder level whose on-hand quantity is at or below it."""
    wids = [warehouse_id] if warehouse_id else visible_warehouse_ids(db, access)
    totals = (
        select(StockBalance.product_id.label("pid"),
               func.sum(StockBalance.quantity).label("qty"),
               func.sum(StockBalance.value).label("val"))
        .where(StockBalance.warehouse_id.in_(wids))
        .group_by(StockBalance.product_id)
        .subquery()
    )
    rows = db.execute(
        select(Product, totals.c.qty, totals.c.val)
        .outerjoin(totals, totals.c.pid == Product.id)
        .where(Product.business_id == access.business.id, Product.is_active.is_(True),
               Product.track_inventory.is_(True), Product.reorder_level.is_not(None))
        .order_by(Product.name)
    ).all()
    out = []
    for p, qty, val in rows:
        on_hand = Decimal(qty) if qty is not None else ZERO
        if on_hand <= p.reorder_level:
            out.append(StockLevel(p.id, p.sku, p.name, on_hand,
                                  Decimal(val) if val is not None else ZERO,
                                  p.reorder_level, p.reorder_quantity))
    return out


@dataclass
class WarehouseValue:
    warehouse_id: uuid.UUID
    code: str
    name: str
    quantity_lines: int
    value: Decimal


def valuation(db: Session, access: BusinessAccess) -> tuple[list[WarehouseValue], Decimal]:
    wids = visible_warehouse_ids(db, access)
    rows = db.execute(
        select(Warehouse, func.count(StockBalance.id), func.coalesce(func.sum(StockBalance.value), 0))
        .outerjoin(StockBalance, and_(StockBalance.warehouse_id == Warehouse.id,
                                      StockBalance.quantity > 0))
        .where(Warehouse.id.in_(wids))
        .group_by(Warehouse.id)
        .order_by(Warehouse.code)
    ).all()
    # SUM/COALESCE keep the Money column type, so values arrive as exact Decimals on every DB.
    out = [WarehouseValue(w.id, w.code, w.name, n, Decimal(v)) for w, n, v in rows]
    return out, sum((r.value for r in out), ZERO)


def ledger(db: Session, access: BusinessAccess, *, product_id: uuid.UUID | None,
           warehouse_id: uuid.UUID | None, date_from: date | None, date_to: date | None) -> Select:
    wids = visible_warehouse_ids(db, access)
    stmt = (
        select(StockLedgerEntry)
        .where(StockLedgerEntry.business_id == access.business.id,
               StockLedgerEntry.warehouse_id.in_(wids))
        .order_by(StockLedgerEntry.posted_at.desc(), StockLedgerEntry.id)
    )
    if product_id:
        stmt = stmt.where(StockLedgerEntry.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(StockLedgerEntry.warehouse_id == warehouse_id)
    if date_from:
        stmt = stmt.where(StockLedgerEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(StockLedgerEntry.entry_date <= date_to)
    return stmt


def expiring_batches(db: Session, access: BusinessAccess, *, within_days: int,
                     today: date) -> Select:
    """Batches still in stock that expire within ``within_days`` (already expired included)."""
    wids = visible_warehouse_ids(db, access)
    return (
        select(StockBalance, Product, Warehouse, Batch)
        .join(Product, Product.id == StockBalance.product_id)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .join(Batch, Batch.id == StockBalance.batch_id)
        .where(StockBalance.business_id == access.business.id,
               StockBalance.warehouse_id.in_(wids), StockBalance.quantity > 0,
               Batch.expiry_date.is_not(None),
               Batch.expiry_date <= today + timedelta(days=within_days))
        .order_by(Batch.expiry_date, Product.name)
    )


def serials(db: Session, access: BusinessAccess, *, product_id: uuid.UUID | None,
            status: SerialStatus | None, q: str | None) -> Select:
    wids = visible_warehouse_ids(db, access)
    stmt = (select(SerialNumber).where(SerialNumber.business_id == access.business.id)
            .order_by(SerialNumber.serial_no))
    # Serials in stock elsewhere are hidden; issued serials stay searchable for warranty lookups.
    stmt = stmt.where(or_(SerialNumber.warehouse_id.in_(wids), SerialNumber.warehouse_id.is_(None)))
    if product_id:
        stmt = stmt.where(SerialNumber.product_id == product_id)
    if status:
        stmt = stmt.where(SerialNumber.status == status)
    if q:
        stmt = stmt.where(SerialNumber.serial_no == q)
    return stmt
