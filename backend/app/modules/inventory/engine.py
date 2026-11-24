"""The stock posting engine. Every change to stock in the ERP goes through ``StockEngine.post``.

Guarantees (all inside the caller's database transaction):

* **Atomic**: either every movement of a document posts, or none does.
* **No overselling**: balance rows are locked (``FOR UPDATE`` on PostgreSQL; SQLite write
  transactions are serialized) before availability is checked. Locks are taken in a fixed
  order to avoid deadlocks between concurrent documents.
* **Exact values**: costs use Decimal; FIFO layers carry their remaining value so issuing the
  last unit takes exactly what is left; weighted average issues the whole value when the whole
  quantity leaves. Ledger sums always equal balances.
* **Reversible**: reversing an entry restores exactly the original quantity, value and (FIFO)
  cost layers, and moves the same serial numbers back.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, ValidationFailed
from app.modules.businesses.models import Business, InventoryValuation
from app.modules.catalog.models import Product, Unit
from app.modules.inventory.models import (
    NO_BATCH,
    Batch,
    CostLayer,
    LayerConsumption,
    SerialNumber,
    SerialStatus,
    StockBalance,
    StockEntrySerial,
    StockLedgerEntry,
)

Q4 = Decimal("0.0001")
ZERO = Decimal(0)


def q4(d: Decimal) -> Decimal:
    return d.quantize(Q4, rounding=ROUND_HALF_UP)


class InsufficientStock(Conflict):
    code = "insufficient_stock"


@dataclass
class Movement:
    """One signed quantity change for a product in a warehouse.

    Inbound (quantity > 0) needs ``unit_cost`` or an exact ``value``. Outbound is valued by the
    engine. ``carry_from`` makes an inbound movement take its value, cost layers and serials
    from an earlier outbound movement in the same post (transfers). ``reverses`` undoes a
    previously posted ledger entry exactly.
    """

    product: Product
    warehouse_id: uuid.UUID
    quantity: Decimal = ZERO
    batch_id: uuid.UUID | None = None
    unit_cost: Decimal | None = None
    value: Decimal | None = None
    serials: list[str] = field(default_factory=list)
    line_id: uuid.UUID | None = None
    carry_from: int | None = None
    reverses: StockLedgerEntry | None = None

    @property
    def batch_key(self) -> str:
        return str(self.batch_id) if self.batch_id else NO_BATCH


@dataclass
class _Lot:
    """A slice of stock leaving or entering: quantity, exact value, original receipt date."""

    quantity: Decimal
    value: Decimal
    received_date: date


@dataclass
class PostedMovement:
    entry: StockLedgerEntry
    lots: list[_Lot]
    serial_ids: list[uuid.UUID]


class StockEngine:
    def __init__(self, db: Session, business: Business) -> None:
        self.db = db
        self.business = business
        self.fifo = business.inventory_valuation == InventoryValuation.FIFO
        self._units: dict[uuid.UUID, Unit] = {}

    # ------------------------------------------------------------------ public API

    def lock_balance(
        self, product_id: uuid.UUID, warehouse_id: uuid.UUID, batch_id: uuid.UUID | None
    ) -> StockBalance:
        """Lock (creating if needed) the balance row and return it with fresh values."""
        key = str(batch_id) if batch_id else NO_BATCH
        stmt = (
            select(StockBalance)
            .where(
                StockBalance.product_id == product_id,
                StockBalance.warehouse_id == warehouse_id,
                StockBalance.batch_key == key,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        bal = self.db.scalar(stmt)
        if bal is None:
            try:
                with self.db.begin_nested():
                    self.db.add(StockBalance(
                        business_id=self.business.id, product_id=product_id,
                        warehouse_id=warehouse_id, batch_key=key, batch_id=batch_id,
                        quantity=ZERO, value=ZERO,
                    ))
            except IntegrityError:
                pass  # inserted concurrently; fall through to the locked read
            bal = self.db.scalar(stmt)
            assert bal is not None
        return bal

    def post(
        self,
        movements: list[Movement],
        *,
        entry_date: date,
        source_type: str,
        source_id: uuid.UUID,
    ) -> list[PostedMovement]:
        for mv in movements:
            self._resolve_reversal(mv)
            self._validate(mv)
        for key in sorted({(str(m.product.id), str(m.warehouse_id), m.batch_key) for m in movements}):
            m = next(x for x in movements
                     if (str(x.product.id), str(x.warehouse_id), x.batch_key) == key)
            self.lock_balance(m.product.id, m.warehouse_id, m.batch_id)

        posted: list[PostedMovement] = []
        for mv in movements:
            if mv.quantity > 0:
                posted.append(self._inbound(mv, posted, entry_date, source_type, source_id))
            else:
                posted.append(self._outbound(mv, entry_date, source_type, source_id))
        self.db.flush()
        return posted

    def on_hand(self, product_id: uuid.UUID, warehouse_ids: list[uuid.UUID] | None = None) -> Decimal:
        stmt = select(func.coalesce(func.sum(StockBalance.quantity), 0)).where(
            StockBalance.product_id == product_id
        )
        if warehouse_ids is not None:
            stmt = stmt.where(StockBalance.warehouse_id.in_(warehouse_ids))
        return Decimal(self.db.scalar(stmt) or 0)

    # ------------------------------------------------------------------ validation

    def _unit(self, product: Product) -> Unit:
        if product.unit_id not in self._units:
            self._units[product.unit_id] = self.db.get(Unit, product.unit_id)  # type: ignore[assignment]
        return self._units[product.unit_id]

    def _resolve_reversal(self, mv: Movement) -> None:
        e = mv.reverses
        if e is None:
            return
        if self.db.scalar(select(StockLedgerEntry.id).where(StockLedgerEntry.reverses_entry_id == e.id)):
            raise Conflict("This stock entry has already been reversed")
        mv.quantity = -e.quantity
        mv.warehouse_id = e.warehouse_id
        mv.batch_id = e.batch_id
        mv.serials = list(self.db.scalars(
            select(SerialNumber.serial_no)
            .join(StockEntrySerial, StockEntrySerial.serial_id == SerialNumber.id)
            .where(StockEntrySerial.entry_id == e.id)
        ))
        if mv.quantity > 0:
            mv.value = -e.value

    def _validate(self, mv: Movement) -> None:
        p = mv.product
        name = f"{p.name} ({p.sku})"
        if p.business_id != self.business.id:
            raise ValidationFailed("Product belongs to another business")
        if not p.track_inventory:
            raise ValidationFailed(f"{name} does not track inventory")
        if mv.quantity == 0:
            raise ValidationFailed(f"{name}: quantity cannot be zero")
        decimals = self._unit(p).decimals
        if mv.quantity != mv.quantity.quantize(Decimal(1).scaleb(-decimals)):
            raise ValidationFailed(f"{name}: quantity allows at most {decimals} decimal places")
        if p.batch_tracking and mv.batch_id is None:
            raise ValidationFailed(f"{name} is batch-tracked: a batch is required")
        if not p.batch_tracking and mv.batch_id is not None:
            raise ValidationFailed(f"{name} is not batch-tracked")
        if mv.batch_id is not None:
            batch = self.db.get(Batch, mv.batch_id)
            if batch is None or batch.product_id != p.id:
                raise ValidationFailed(f"{name}: batch does not belong to this product")
        if p.serial_tracking:
            serials = [s.strip() for s in mv.serials if s.strip()]
            if len(set(serials)) != len(serials):
                raise ValidationFailed(f"{name}: duplicate serial numbers")
            if Decimal(len(serials)) != abs(mv.quantity):
                raise ValidationFailed(
                    f"{name} is serial-tracked: give exactly {abs(mv.quantity)} serial numbers")
            mv.serials = serials
        elif mv.serials:
            raise ValidationFailed(f"{name} is not serial-tracked")
        if mv.quantity > 0 and mv.carry_from is None and mv.reverses is None:
            if mv.value is None and mv.unit_cost is None:
                raise ValidationFailed(f"{name}: unit cost is required for stock coming in")
            if (mv.unit_cost is not None and mv.unit_cost < 0) or (mv.value is not None and mv.value < 0):
                raise ValidationFailed(f"{name}: cost cannot be negative")

    # ------------------------------------------------------------------ posting

    def _entry(self, mv: Movement, bal: StockBalance, qty: Decimal, value: Decimal,
               entry_date: date, source_type: str, source_id: uuid.UUID) -> StockLedgerEntry:
        entry = StockLedgerEntry(
            business_id=self.business.id, product_id=mv.product.id, warehouse_id=mv.warehouse_id,
            batch_id=mv.batch_id, entry_date=entry_date, source_type=source_type,
            source_id=source_id, source_line_id=mv.line_id, quantity=qty,
            unit_cost=q4(abs(value) / abs(qty)), value=value,
            balance_quantity=bal.quantity, balance_value=bal.value,
            reverses_entry_id=mv.reverses.id if mv.reverses else None,
        )
        self.db.add(entry)
        self.db.flush()
        return entry

    def _inbound(self, mv: Movement, posted: list[PostedMovement], entry_date: date,
                 source_type: str, source_id: uuid.UUID) -> PostedMovement:
        qty = mv.quantity
        if mv.carry_from is not None:
            src = posted[mv.carry_from]
            lots = [_Lot(lot.quantity, lot.value, lot.received_date) for lot in src.lots]
            mv.serials = list(self.db.scalars(
                select(SerialNumber.serial_no).where(SerialNumber.id.in_(src.serial_ids))
            )) if src.serial_ids else []
        elif mv.reverses is not None and self.fifo:
            lots = [
                _Lot(c.quantity, c.value, layer.received_date)
                for c, layer in self.db.execute(
                    select(LayerConsumption, CostLayer)
                    .join(CostLayer, CostLayer.id == LayerConsumption.layer_id)
                    .where(LayerConsumption.entry_id == mv.reverses.id)
                )
            ]
        else:
            value = mv.value if mv.value is not None else q4(qty * mv.unit_cost)  # type: ignore[operator]
            lots = [_Lot(qty, value, entry_date)]
        value = sum((lot.value for lot in lots), ZERO)

        bal = self.lock_balance(mv.product.id, mv.warehouse_id, mv.batch_id)
        bal.quantity += qty
        bal.value += value
        entry = self._entry(mv, bal, qty, value, entry_date, source_type, source_id)
        if self.fifo:
            for lot in lots:
                self.db.add(CostLayer(
                    business_id=self.business.id, product_id=mv.product.id,
                    warehouse_id=mv.warehouse_id, batch_key=mv.batch_key,
                    source_entry_id=entry.id, received_date=lot.received_date,
                    original_quantity=lot.quantity, remaining_quantity=lot.quantity,
                    unit_cost=q4(lot.value / lot.quantity), remaining_value=lot.value,
                ))
        serial_ids = self._serials_in(mv, entry) if mv.product.serial_tracking else []
        return PostedMovement(entry, lots, serial_ids)

    def _outbound(self, mv: Movement, entry_date: date, source_type: str,
                  source_id: uuid.UUID) -> PostedMovement:
        need = -mv.quantity
        bal = self.lock_balance(mv.product.id, mv.warehouse_id, mv.batch_id)
        if mv.reverses is not None and bal.quantity < need:
            raise Conflict("Stock received by this entry has already been issued; "
                           "reverse the later issues first")
        if bal.quantity < need:
            raise InsufficientStock(
                f"Not enough stock of {mv.product.name}: {bal.quantity} available, {need} needed",
                details={"product_id": str(mv.product.id), "available": str(bal.quantity),
                         "required": str(need)})

        consumptions: list[tuple[CostLayer, Decimal, Decimal]] = []
        if self.fifo:
            stmt = (
                select(CostLayer)
                .where(CostLayer.product_id == mv.product.id,
                       CostLayer.warehouse_id == mv.warehouse_id,
                       CostLayer.batch_key == mv.batch_key,
                       CostLayer.remaining_quantity > 0)
                .order_by(CostLayer.received_date, CostLayer.created_at, CostLayer.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if mv.reverses is not None:  # undo a receipt: take back exactly its own layers
                stmt = stmt.where(CostLayer.source_entry_id == mv.reverses.id)
            layers = list(self.db.scalars(stmt))
            if mv.reverses is not None:
                untouched = all(lay.remaining_quantity == lay.original_quantity for lay in layers)
                total = sum((lay.remaining_quantity for lay in layers), ZERO)
                if not untouched or total != need:
                    raise Conflict("Stock received by this entry has already been issued; "
                                   "reverse the later issues first")
            remaining = need
            for layer in layers:
                if remaining == 0:
                    break
                take = min(remaining, layer.remaining_quantity)
                v = layer.remaining_value if take == layer.remaining_quantity else q4(take * layer.unit_cost)
                layer.remaining_quantity -= take
                layer.remaining_value -= v
                consumptions.append((layer, take, v))
                remaining -= take
            if remaining != 0:
                raise RuntimeError("Cost layers out of sync with stock balance")
            value_out = sum((v for _, _, v in consumptions), ZERO)
        elif mv.reverses is not None:
            value_out = bal.value if need == bal.quantity else min(-mv.reverses.value, bal.value)
        else:
            value_out = bal.value if need == bal.quantity else q4(bal.value * need / bal.quantity)

        bal.quantity -= need
        bal.value -= value_out
        entry = self._entry(mv, bal, -need, -value_out, entry_date, source_type, source_id)
        for layer, take, v in consumptions:
            self.db.add(LayerConsumption(entry_id=entry.id, layer_id=layer.id, quantity=take,
                                         unit_cost=layer.unit_cost, value=v))
        lots = ([_Lot(t, v, layer.received_date) for layer, t, v in consumptions]
                if consumptions else [_Lot(need, value_out, entry_date)])
        serial_ids = self._serials_out(mv, entry) if mv.product.serial_tracking else []
        return PostedMovement(entry, lots, serial_ids)

    # ------------------------------------------------------------------ serials

    def _serial_rows(self, mv: Movement) -> dict[str, SerialNumber]:
        rows = self.db.scalars(
            select(SerialNumber)
            .where(SerialNumber.product_id == mv.product.id, SerialNumber.serial_no.in_(mv.serials))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {r.serial_no: r for r in rows}

    def _serials_in(self, mv: Movement, entry: StockLedgerEntry) -> list[uuid.UUID]:
        existing = self._serial_rows(mv)
        ids = []
        for s in mv.serials:
            row = existing.get(s)
            if row is not None and row.status == SerialStatus.IN_STOCK:
                raise Conflict(f"Serial {s} of {mv.product.name} is already in stock")
            if row is None:
                row = SerialNumber(business_id=self.business.id, product_id=mv.product.id,
                                   serial_no=s, status=SerialStatus.IN_STOCK)
                self.db.add(row)
            row.status = SerialStatus.IN_STOCK
            row.warehouse_id = mv.warehouse_id
            row.batch_id = mv.batch_id
            self.db.flush()
            self.db.add(StockEntrySerial(entry_id=entry.id, serial_id=row.id))
            ids.append(row.id)
        return ids

    def _serials_out(self, mv: Movement, entry: StockLedgerEntry) -> list[uuid.UUID]:
        existing = self._serial_rows(mv)
        ids = []
        for s in mv.serials:
            row = existing.get(s)
            if (row is None or row.status != SerialStatus.IN_STOCK
                    or row.warehouse_id != mv.warehouse_id or row.batch_id != mv.batch_id):
                raise ValidationFailed(
                    f"Serial {s} of {mv.product.name} is not in stock in this warehouse")
            row.status = SerialStatus.OUT
            row.warehouse_id = None
            self.db.add(StockEntrySerial(entry_id=entry.id, serial_id=row.id))
            ids.append(row.id)
        return ids


def verify_balances(db: Session, business_id: uuid.UUID) -> list[str]:
    """Consistency check: every balance equals the sum of its ledger entries, and (FIFO) the
    open cost layers add up to the balance. Returns a list of problems (empty = consistent)."""
    problems: list[str] = []
    sums: dict[tuple, tuple[Decimal, Decimal]] = defaultdict(lambda: (ZERO, ZERO))
    for e in db.scalars(select(StockLedgerEntry).where(StockLedgerEntry.business_id == business_id)):
        k = (e.product_id, e.warehouse_id, str(e.batch_id) if e.batch_id else NO_BATCH)
        q, v = sums[k]
        sums[k] = (q + e.quantity, v + e.value)
    layers: dict[tuple, tuple[Decimal, Decimal]] = defaultdict(lambda: (ZERO, ZERO))
    for lay in db.scalars(select(CostLayer).where(CostLayer.business_id == business_id)):
        k = (lay.product_id, lay.warehouse_id, lay.batch_key)
        q, v = layers[k]
        layers[k] = (q + lay.remaining_quantity, v + lay.remaining_value)
    business = db.get(Business, business_id)
    fifo = business is not None and business.inventory_valuation == InventoryValuation.FIFO
    for b in db.scalars(select(StockBalance).where(StockBalance.business_id == business_id)):
        k = (b.product_id, b.warehouse_id, b.batch_key)
        if sums[k] != (b.quantity, b.value):
            problems.append(f"balance {k} = {(b.quantity, b.value)} but ledger sums to {sums[k]}")
        if fifo and layers[k] != (b.quantity, b.value):
            problems.append(f"balance {k} = {(b.quantity, b.value)} but layers hold {layers[k]}")
        if b.quantity == 0 and b.value != 0:
            problems.append(f"balance {k} has zero quantity but value {b.value}")
    return problems
