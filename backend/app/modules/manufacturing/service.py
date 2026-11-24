"""Manufacturing: recipes, material planning and production runs.

A production run is one stock document (kind ``production``):

* every recipe material leaves the warehouse (batch-tracked materials first-expiry-first,
  never expired), valued by the business's stock method (weighted average or FIFO);
* the finished product enters as one batch, valued at material cost + overhead;
* the books get Dr Inventory / Cr Production Overhead Absorbed for the overhead only (materials
  just move from one stock line to another, both inside Inventory).

With quality control, the finished batch is *on hold* (cannot be sold) until released; a
rejected batch is written off with a stock adjustment. A run is cancelled by exact reversal while
its finished goods are still in stock.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.catalog.models import Product
from app.modules.events import service as events
from app.modules.inventory import service as inv
from app.modules.inventory.engine import InsufficientStock, Movement, StockEngine
from app.modules.inventory.expiry import available_batches
from app.modules.inventory.models import AdjustmentReason, Batch, StockBalance, StockDocumentKind
from app.modules.manufacturing.models import ProductionRun, ProductionStatus, QcStatus, Recipe, RecipeLine

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def _q(d: Decimal, places: str = "0.001") -> Decimal:
    return d.quantize(Decimal(places), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------- recipes


def _product(db: Session, access: BusinessAccess, pid: uuid.UUID, what: str) -> Product:
    p = db.get(Product, pid)
    if p is None or p.business_id != access.business.id:
        raise ValidationFailed(f"{what} not found")
    if not p.track_inventory:
        raise ValidationFailed(f"{p.name} does not track stock; switch on stock tracking first")
    return p


def save_recipe(db: Session, access: BusinessAccess, data: dict[str, Any], r: Recipe | None = None) -> Recipe:
    access.require("production.manage")
    lines = data.pop("lines", None)
    name = data.get("name", r.name if r else "")
    dup = select(Recipe.id).where(Recipe.business_id == access.business.id, Recipe.name == name)
    if r is not None:
        dup = dup.where(Recipe.id != r.id)
    if db.scalar(dup):
        raise ValidationFailed(f"A recipe named {name} already exists")
    if r is None:
        r = Recipe(business_id=access.business.id)
        db.add(r)
    for k, v in data.items():
        setattr(r, k, v)
    out = _product(db, access, r.product_id, "Finished product")
    if lines is not None:
        if not lines:
            raise ValidationFailed("A recipe needs at least one material")
        ids = [ln["product_id"] for ln in lines]
        if len(set(ids)) != len(ids):
            raise ValidationFailed("A material appears twice; combine the quantities")
        if out.id in ids:
            raise ValidationFailed("A product cannot be a material of itself")
        for ln in lines:
            _product(db, access, ln["product_id"], "Material")
        r.lines.clear()
        db.flush()
        r.lines.extend(RecipeLine(line_no=i, product_id=ln["product_id"], quantity=Decimal(ln["quantity"]),
                                  wastage_percent=Decimal(ln.get("wastage_percent") or 0)) for i, ln in enumerate(lines, 1))
    db.flush()
    audit.record(db, action="recipe.saved", entity_type="recipe", entity_id=r.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": r.name, "materials": len(r.lines)})
    return r


def get_recipe(db: Session, access: BusinessAccess, rid: uuid.UUID) -> Recipe:
    r = db.get(Recipe, rid)
    if r is None or r.business_id != access.business.id:
        raise NotFound("Recipe not found")
    return r


def delete_recipe(db: Session, access: BusinessAccess, r: Recipe) -> None:
    access.require("production.manage")
    if db.scalar(select(ProductionRun.id).where(ProductionRun.recipe_id == r.id).limit(1)):
        raise ValidationFailed("This recipe has production history; switch it off instead")
    db.delete(r)
    db.flush()


# ---------------------------------------------------------------------------- planning


def requirements(db: Session, r: Recipe, quantity: Decimal) -> list[tuple[RecipeLine, Decimal]]:
    """Material quantities for ``quantity`` of output, wastage included, rounded *up* to what each
    material's unit allows (a jar is used whole; kilograms to three decimals)."""
    from app.modules.catalog.models import Unit

    factor = quantity / r.output_quantity
    out = []
    for ln in r.lines:
        unit = db.get(Unit, db.get(Product, ln.product_id).unit_id)
        step = Decimal(1).scaleb(-(unit.decimals if unit else 3))
        need = (ln.quantity * factor * (HUNDRED + ln.wastage_percent) / HUNDRED).quantize(step, rounding=ROUND_UP)
        out.append((ln, need))
    return out


def plan(db: Session, access: BusinessAccess, r: Recipe, quantity: Decimal, warehouse_id: uuid.UUID) -> dict[str, Any]:
    on = local_today()
    engine = StockEngine(db, access.business)
    rows, cost, short = [], ZERO, False
    for ln, need in requirements(db, r, quantity):
        p = db.get(Product, ln.product_id)
        if p.batch_tracking:
            have = sum((q for _, q in available_batches(db, access.business.id, p.id, warehouse_id, on=on)), ZERO)
        else:
            have = engine.on_hand(p.id, [warehouse_id])
        bal = db.execute(select(func.coalesce(func.sum(StockBalance.value), 0), func.coalesce(func.sum(StockBalance.quantity), 0))
                         .where(StockBalance.product_id == p.id, StockBalance.warehouse_id == warehouse_id)).one()
        unit = (Decimal(bal[0]) / Decimal(bal[1])) if Decimal(bal[1]) > 0 else (p.purchase_price or ZERO)
        est = _q(unit * need, "0.01")
        cost += est
        short = short or have < need
        rows.append({"product_id": p.id, "name": p.name, "sku": p.sku, "required": need, "available": have,
                     "shortage": max(need - have, ZERO), "unit_cost": _q(unit, "0.0001"), "estimated_cost": est})
    overhead = _q(r.overhead * quantity / r.output_quantity, "0.01")
    total = cost + overhead
    return {"recipe_id": r.id, "quantity": quantity, "materials": rows, "material_cost": cost, "overhead": overhead,
            "total_cost": total, "unit_cost": _q(total / quantity, "0.0001"), "can_produce": not short}


# ---------------------------------------------------------------------------- production


def produce(db: Session, access: BusinessAccess, r: Recipe, *, quantity: Decimal, warehouse_id: uuid.UUID,
            doc_date: date, batch_no: str | None, expiry_date: date | None, mrp: Decimal | None,
            overhead: Decimal | None, notes: str | None) -> ProductionRun:
    access.require("production.manage")
    access.require("stock.edit")
    if not r.is_active:
        raise ValidationFailed("This recipe is switched off")
    if quantity <= 0:
        raise ValidationFailed("Quantity must be positive")
    wh = inv._active_warehouse(db, access, warehouse_id)
    out = _product(db, access, r.product_id, "Finished product")
    if out.batch_tracking and not batch_no:
        raise ValidationFailed(f"{out.name} is batch-tracked: give the batch number")
    if out.batch_tracking and expiry_date is None and r.shelf_life_days:
        expiry_date = doc_date + timedelta(days=r.shelf_life_days)
    held = r.requires_qc and out.batch_tracking

    doc = inv._new_document(db, access, StockDocumentKind.PRODUCTION, doc_date=doc_date, warehouse=wh,
                            reference=f"{r.name} × {quantity.normalize():f}", notes=notes)
    engine = StockEngine(db, access.business)
    before: dict[uuid.UUID, Decimal] = {}
    outbound: list[Movement] = []
    line_no = 0
    for ln, need in requirements(db, r, quantity):
        p = db.get(Product, ln.product_id)
        before.setdefault(p.id, engine.on_hand(p.id))
        if p.batch_tracking:
            left = need
            for b, have in available_batches(db, access.business.id, p.id, wh.id, on=doc_date):
                take = min(have, left)
                line_no += 1
                sl = inv._add_line(doc, line_no, p, b, -take, None, [])
                db.flush()
                outbound.append(Movement(p, wh.id, -take, b.id, line_id=sl.id))
                left -= take
                if left == 0:
                    break
            if left > 0:
                raise InsufficientStock(f"Not enough usable {p.name}: {(need - left).normalize():f} available, "
                                        f"{need.normalize():f} needed")
        else:
            line_no += 1
            sl = inv._add_line(doc, line_no, p, None, -need, None, [])
            db.flush()
            outbound.append(Movement(p, wh.id, -need, None, line_id=sl.id))
    posted = engine.post(outbound, entry_date=doc_date, source_type=inv.SOURCE, source_id=doc.id)
    material_cost = -sum((pm.entry.value for pm in posted), ZERO)
    ovh = overhead if overhead is not None else _q(r.overhead * quantity / r.output_quantity, "0.01")
    total = material_cost + ovh

    batch: Batch | None = None
    if out.batch_tracking:
        batch = db.scalar(select(Batch).where(Batch.product_id == out.id, Batch.batch_no == batch_no))
        if batch is not None:
            raise Conflict(f"Batch {batch_no} of {out.name} already exists; use a new batch number")
        batch = Batch(business_id=access.business.id, product_id=out.id, batch_no=batch_no, mfg_date=doc_date,
                      expiry_date=expiry_date, mrp=mrp, on_hold=held)
        db.add(batch)
        db.flush()
    before.setdefault(out.id, engine.on_hand(out.id))
    line_no += 1
    fg = inv._add_line(doc, line_no, out, batch, quantity, _q(total / quantity, "0.0001"), [])
    db.flush()
    posted += engine.post([Movement(out, wh.id, quantity, batch.id if batch else None, value=total, line_id=fg.id)],
                          entry_date=doc_date, source_type=inv.SOURCE, source_id=doc.id)

    from app.modules.accounting.posting import post_stock_document

    post_stock_document(db, access.business, doc, user_id=access.user.id)
    inv._emit(db, access, doc, posted, before, engine)
    run = ProductionRun(
        business_id=access.business.id, number=doc.number, recipe_id=r.id, product_id=out.id, warehouse_id=wh.id,
        stock_document_id=doc.id, doc_date=doc_date, quantity=quantity, batch_no=batch_no, expiry_date=expiry_date,
        material_cost=material_cost, overhead=ovh, total_cost=total, unit_cost=_q(total / quantity, "0.0001"),
        status=ProductionStatus.COMPLETED, qc_status=QcStatus.PENDING if held else QcStatus.NOT_REQUIRED,
        notes=notes, created_by_id=access.user.id)
    db.add(run)
    db.flush()
    audit.record(db, action="production.completed", entity_type="production_run", entity_id=run.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": run.number, "product": out.name, "quantity": quantity, "total_cost": total})
    events.emit(db, "production.completed", aggregate_type="production_run", aggregate_id=run.id,
                business_id=access.business.id,
                payload={"id": run.id, "number": run.number, "product_id": out.id, "name": out.name,
                         "quantity": quantity, "batch_no": batch_no, "unit_cost": run.unit_cost,
                         "qc_status": run.qc_status.value})
    return run


def get_run(db: Session, access: BusinessAccess, rid: uuid.UUID) -> ProductionRun:
    run = db.get(ProductionRun, rid)
    if run is None or run.business_id != access.business.id:
        raise NotFound("Production run not found")
    inv.get_warehouse(db, access, run.warehouse_id)
    return run


def quality_decision(db: Session, access: BusinessAccess, run: ProductionRun, *, release: bool,
                     notes: str | None) -> ProductionRun:
    access.require("production.manage")
    if run.qc_status != QcStatus.PENDING or run.status != ProductionStatus.COMPLETED:
        raise Conflict("This batch is not waiting for quality control")
    batch = db.scalar(select(Batch).where(Batch.product_id == run.product_id, Batch.batch_no == run.batch_no))
    run.qc_notes, run.qc_by_id, run.qc_at = notes, access.user.id, datetime.now(UTC)
    if release:
        batch.on_hold = False
        run.qc_status = QcStatus.RELEASED
    else:
        have = db.scalar(select(func.coalesce(func.sum(StockBalance.quantity), 0)).where(StockBalance.batch_id == batch.id))
        if have and Decimal(have) > 0:
            batch.on_hold = False  # the write-off must be able to take it out
            inv.create_adjustment(db, access, warehouse_id=run.warehouse_id, doc_date=local_today(),
                                  reason=AdjustmentReason.OTHER, reference=f"QC rejected {run.number}",
                                  notes=notes or "Rejected in quality control",
                                  lines=[inv.LineIn(product_id=run.product_id, quantity=-Decimal(have),
                                                    batch_no=run.batch_no)])
        run.qc_status = QcStatus.REJECTED
    db.flush()
    audit.record(db, action=f"production.qc_{'released' if release else 'rejected'}", entity_type="production_run",
                 entity_id=run.id, business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": run.number, "notes": notes})
    return run


def cancel(db: Session, access: BusinessAccess, run: ProductionRun, *, reason: str, on: date) -> ProductionRun:
    access.require("production.manage")
    if run.status == ProductionStatus.CANCELLED:
        raise Conflict("This production run is already cancelled")
    if run.qc_status == QcStatus.REJECTED:
        raise Conflict("A rejected batch was written off; it cannot be cancelled")
    batch = db.scalar(select(Batch).where(Batch.product_id == run.product_id, Batch.batch_no == run.batch_no)) \
        if run.batch_no else None
    if batch is not None:
        batch.on_hold = False
    doc = inv.get_document(db, access, run.stock_document_id)
    inv.cancel_document(db, access, doc, reason=reason, on=on)
    run.status = ProductionStatus.CANCELLED
    db.flush()
    return run


def summary_by_product(runs: list[ProductionRun]) -> dict[uuid.UUID, Decimal]:
    out: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for r in runs:
        if r.status == ProductionStatus.COMPLETED:
            out[r.product_id] += r.quantity
    return out
