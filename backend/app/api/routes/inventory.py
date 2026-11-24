"""Warehouses, inventory documents and stock reports."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select

from app.api.deps import DB, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas_inventory import (
    AdjustmentIn,
    BalanceOut,
    BatchOut,
    CancelIn,
    CountIn,
    DocumentOut,
    DocumentSummaryOut,
    LedgerEntryOut,
    LowStockOut,
    OpeningIn,
    SerialOut,
    TransferIn,
    ValuationOut,
    WarehouseValueOut,
)
from app.api.schemas_masters import WarehouseIn, WarehouseOut, WarehouseUpdateIn
from app.core.clock import local_today
from app.modules.access.service import BusinessAccess
from app.modules.catalog.service import get_product
from app.modules.inventory import reports, service
from app.modules.inventory.models import (
    Batch,
    SerialStatus,
    StockDocument,
    StockDocumentKind,
    StockDocumentStatus,
)
from app.modules.inventory.service import LineIn

router = APIRouter(prefix="/businesses/{business_id}", tags=["inventory"])

ViewStock = Annotated[BusinessAccess, Depends(require("stock.view"))]
EditStock = Annotated[BusinessAccess, Depends(require("stock.edit"))]
ManageWarehouses = Annotated[BusinessAccess, Depends(require("warehouses.manage"))]
ValueStock = Annotated[BusinessAccess, Depends(require("stock.view", "stock_value.view"))]


def _lines(items: list) -> list[LineIn]:
    return [LineIn(**i.model_dump()) for i in items]


def _hide_costs(access: BusinessAccess, entry: LedgerEntryOut) -> LedgerEntryOut:
    if not access.can("stock_value.view"):
        entry.unit_cost = entry.value = entry.balance_value = None
    return entry


def _doc_out(db: DB, access: BusinessAccess, doc: StockDocument) -> DocumentOut:
    out = DocumentOut.model_validate(doc)
    out.entries = [_hide_costs(access, LedgerEntryOut.model_validate(e))
                   for e in service.document_entries(db, doc)]
    if not access.can("stock_value.view"):
        for ln in out.lines:
            ln.unit_cost = None
    return out


# ---------------------------------------------------------------------------- warehouses


@router.get("/warehouses", response_model=list[WarehouseOut], tags=["warehouses"])
def list_warehouses(access: Annotated[BusinessAccess, Depends(require("business.view"))], db: DB,
                    include_inactive: bool = False) -> list[WarehouseOut]:
    return [WarehouseOut.model_validate(w)
            for w in service.list_warehouses(db, access, include_inactive=include_inactive)]


@router.post("/warehouses", response_model=WarehouseOut, status_code=status.HTTP_201_CREATED,
             tags=["warehouses"])
def create_warehouse(body: WarehouseIn, access: ManageWarehouses, db: DB) -> WarehouseOut:
    w = service.create_warehouse(db, access, body.model_dump())
    db.commit()
    return WarehouseOut.model_validate(w)


@router.patch("/warehouses/{warehouse_id}", response_model=WarehouseOut, tags=["warehouses"])
def update_warehouse(warehouse_id: uuid.UUID, body: WarehouseUpdateIn, access: ManageWarehouses,
                     db: DB) -> WarehouseOut:
    w = service.update_warehouse(db, access, service.get_warehouse(db, access, warehouse_id),
                                 body.model_dump(exclude_unset=True))
    db.commit()
    return WarehouseOut.model_validate(w)


# ---------------------------------------------------------------------------- documents


@router.post("/inventory/opening-stock", response_model=DocumentOut,
             status_code=status.HTTP_201_CREATED)
def opening_stock(body: OpeningIn, access: EditStock, db: DB) -> DocumentOut:
    doc = service.create_opening(db, access, warehouse_id=body.warehouse_id, doc_date=body.doc_date,
                                 lines=_lines(body.lines), reference=body.reference, notes=body.notes)
    db.commit()
    return _doc_out(db, access, doc)


@router.post("/inventory/adjustments", response_model=DocumentOut,
             status_code=status.HTTP_201_CREATED)
def adjustment(body: AdjustmentIn, access: EditStock, db: DB) -> DocumentOut:
    doc = service.create_adjustment(db, access, warehouse_id=body.warehouse_id,
                                    doc_date=body.doc_date, reason=body.reason,
                                    lines=_lines(body.lines), reference=body.reference,
                                    notes=body.notes)
    db.commit()
    return _doc_out(db, access, doc)


@router.post("/inventory/transfers", response_model=DocumentOut,
             status_code=status.HTTP_201_CREATED)
def transfer(body: TransferIn, access: EditStock, db: DB) -> DocumentOut:
    doc = service.create_transfer(db, access, from_warehouse_id=body.from_warehouse_id,
                                  to_warehouse_id=body.to_warehouse_id, doc_date=body.doc_date,
                                  lines=_lines(body.lines), reference=body.reference,
                                  notes=body.notes)
    db.commit()
    return _doc_out(db, access, doc)


@router.post("/inventory/counts", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def physical_count(body: CountIn, access: EditStock, db: DB) -> DocumentOut:
    doc = service.create_count(db, access, warehouse_id=body.warehouse_id, doc_date=body.doc_date,
                               lines=_lines(body.lines), reference=body.reference, notes=body.notes)
    db.commit()
    return _doc_out(db, access, doc)


@router.get("/inventory/documents", response_model=Page[DocumentSummaryOut])
def list_documents(
    access: ViewStock, db: DB, params: Annotated[PageParams, Depends(page_params)],
    kind: StockDocumentKind | None = None, status_filter: StockDocumentStatus | None = None,
    warehouse_id: uuid.UUID | None = None, date_from: date | None = None,
    date_to: date | None = None,
) -> Page[DocumentSummaryOut]:
    wids = service.visible_warehouse_ids(db, access)
    stmt = (select(StockDocument)
            .where(StockDocument.business_id == access.business.id,
                   StockDocument.warehouse_id.in_(wids) | StockDocument.to_warehouse_id.in_(wids))
            .order_by(StockDocument.doc_date.desc(), StockDocument.number.desc()))
    if kind:
        stmt = stmt.where(StockDocument.kind == kind)
    if status_filter:
        stmt = stmt.where(StockDocument.status == status_filter)
    if warehouse_id:
        stmt = stmt.where((StockDocument.warehouse_id == warehouse_id)
                          | (StockDocument.to_warehouse_id == warehouse_id))
    if date_from:
        stmt = stmt.where(StockDocument.doc_date >= date_from)
    if date_to:
        stmt = stmt.where(StockDocument.doc_date <= date_to)
    rows, total = paginate(db, stmt, params)
    return Page(items=[DocumentSummaryOut.model_validate(d) for d in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.get("/inventory/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, access: ViewStock, db: DB) -> DocumentOut:
    return _doc_out(db, access, service.get_document(db, access, document_id))


@router.post("/inventory/documents/{document_id}/cancel", response_model=DocumentOut)
def cancel_document(document_id: uuid.UUID, body: CancelIn, access: EditStock, db: DB) -> DocumentOut:
    doc = service.cancel_document(db, access, service.get_document(db, access, document_id),
                                  reason=body.reason, on=body.cancel_date or local_today())
    db.commit()
    return _doc_out(db, access, doc)


# ---------------------------------------------------------------------------- reports


@router.get("/inventory/balances", response_model=Page[BalanceOut])
def stock_balances(
    access: ViewStock, db: DB, params: Annotated[PageParams, Depends(page_params)],
    warehouse_id: uuid.UUID | None = None, product_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=100), include_zero: bool = False,
) -> Page[BalanceOut]:
    stmt = reports.balances(db, access, warehouse_id=warehouse_id, product_id=product_id, q=q,
                            include_zero=include_zero)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.limit(params.limit).offset(params.offset)).all()
    show_cost = access.can("stock_value.view")
    items = []
    for row in rows:
        r = reports.to_balance_row(*row)
        items.append(BalanceOut(**{**r.__dict__, **({} if show_cost else
                                                     {"value": None, "average_cost": None})}))
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.get("/inventory/ledger", response_model=Page[LedgerEntryOut])
def stock_ledger(
    access: ViewStock, db: DB, params: Annotated[PageParams, Depends(page_params)],
    product_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None,
    date_from: date | None = None, date_to: date | None = None,
) -> Page[LedgerEntryOut]:
    """Stock movement history, newest first."""
    stmt = reports.ledger(db, access, product_id=product_id, warehouse_id=warehouse_id,
                          date_from=date_from, date_to=date_to)
    rows, total = paginate(db, stmt, params)
    return Page(items=[_hide_costs(access, LedgerEntryOut.model_validate(e)) for e in rows],
                total=total, limit=params.limit, offset=params.offset)


@router.get("/inventory/valuation", response_model=ValuationOut)
def stock_valuation(access: ValueStock, db: DB) -> ValuationOut:
    rows, total = reports.valuation(db, access)
    return ValuationOut(
        method=access.business.inventory_valuation.value, total_value=total,
        warehouses=[WarehouseValueOut(warehouse_id=r.warehouse_id, code=r.code, name=r.name,
                                      stock_lines=r.quantity_lines, value=r.value) for r in rows])


@router.get("/inventory/low-stock", response_model=list[LowStockOut])
def low_stock(access: ViewStock, db: DB, warehouse_id: uuid.UUID | None = None) -> list[LowStockOut]:
    if warehouse_id:
        service.get_warehouse(db, access, warehouse_id)
    return [LowStockOut(product_id=r.product_id, sku=r.sku, name=r.name, on_hand=r.on_hand,
                        reorder_level=r.reorder_level, reorder_quantity=r.reorder_quantity)
            for r in reports.low_stock(db, access, warehouse_id=warehouse_id)]


@router.get("/inventory/expiring", response_model=list[BalanceOut])
def expiring(access: ViewStock, db: DB,
             within_days: int = Query(30, ge=0, le=3650)) -> list[BalanceOut]:
    """Batches in stock that expire within N days (already-expired stock included)."""
    show_cost = access.can("stock_value.view")
    out = []
    for row in db.execute(reports.expiring_batches(db, access, within_days=within_days,
                                                   today=local_today())).all():
        r = reports.to_balance_row(*row)
        out.append(BalanceOut(**{**r.__dict__, **({} if show_cost else
                                                   {"value": None, "average_cost": None})}))
    return out


@router.get("/products/{product_id}/batches", response_model=list[BatchOut], tags=["products"])
def product_batches(product_id: uuid.UUID, access: ViewStock, db: DB) -> list[BatchOut]:
    p = get_product(db, access, product_id)
    rows = db.scalars(select(Batch).where(Batch.product_id == p.id)
                      .order_by(Batch.expiry_date, Batch.batch_no))
    return [BatchOut.model_validate(b) for b in rows]


@router.get("/inventory/serials", response_model=Page[SerialOut])
def serial_numbers(
    access: ViewStock, db: DB, params: Annotated[PageParams, Depends(page_params)],
    product_id: uuid.UUID | None = None, status_filter: SerialStatus | None = None,
    q: str | None = Query(None, max_length=80, description="Exact serial number"),
) -> Page[SerialOut]:
    stmt = reports.serials(db, access, product_id=product_id, status=status_filter, q=q)
    rows, total = paginate(db, stmt, params)
    return Page(items=[SerialOut.model_validate(s) for s in rows], total=total,
                limit=params.limit, offset=params.offset)
