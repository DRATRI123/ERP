"""Recipes (bills of materials) and production runs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from app.api.deps import DB, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.core.clock import local_today
from app.modules.access.service import BusinessAccess
from app.modules.catalog.models import Product
from app.modules.inventory.models import Batch
from app.modules.manufacturing import service
from app.modules.manufacturing.models import ProductionRun, ProductionStatus, QcStatus, Recipe

router = APIRouter(prefix="/businesses/{business_id}", tags=["manufacturing"])
View = Annotated[BusinessAccess, Depends(require("stock.view"))]
Manage = Annotated[BusinessAccess, Depends(require("stock.view", "production.manage"))]
Qty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]


class RecipeLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Qty
    wastage_percent: Decimal = Field(Decimal(0), ge=0, le=100, max_digits=8, decimal_places=4)


class RecipeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    product_id: uuid.UUID
    output_quantity: Qty
    overhead: Decimal = Field(Decimal(0), ge=0, max_digits=16, decimal_places=4)
    requires_qc: bool = False
    shelf_life_days: int | None = Field(None, ge=1, le=3650)
    is_active: bool = True
    notes: str | None = Field(None, max_length=2000)
    lines: list[RecipeLineIn] = Field(min_length=1, max_length=100)


class RecipeLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: uuid.UUID
    product_name: str | None = None
    quantity: Decimal
    wastage_percent: Decimal


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    product_id: uuid.UUID
    product_name: str | None = None
    output_quantity: Decimal
    overhead: Decimal
    requires_qc: bool
    shelf_life_days: int | None
    is_active: bool
    notes: str | None
    lines: list[RecipeLineOut]
    updated_at: datetime


class ProduceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: uuid.UUID
    quantity: Qty
    warehouse_id: uuid.UUID
    doc_date: date | None = None
    batch_no: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    expiry_date: date | None = None
    mrp: Decimal | None = Field(None, ge=0, max_digits=16, decimal_places=4)
    overhead: Decimal | None = Field(None, ge=0, max_digits=16, decimal_places=4)
    notes: str | None = Field(None, max_length=2000)


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    number: str
    recipe_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str | None = None
    warehouse_id: uuid.UUID
    stock_document_id: uuid.UUID
    doc_date: date
    quantity: Decimal
    batch_no: str | None
    expiry_date: date | None
    material_cost: Decimal | None
    overhead: Decimal | None
    total_cost: Decimal | None
    unit_cost: Decimal | None
    status: ProductionStatus
    qc_status: QcStatus
    qc_notes: str | None
    qc_at: datetime | None
    notes: str | None
    created_at: datetime


class QcIn(BaseModel):
    release: bool
    notes: str | None = Field(None, max_length=2000)


class CancelIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


def _names(db: DB, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    return dict(db.execute(select(Product.id, Product.name).where(Product.id.in_(ids or {uuid.uuid4()}))).all())


def _recipe_out(db: DB, r: Recipe) -> RecipeOut:
    names = _names(db, {r.product_id, *(ln.product_id for ln in r.lines)})
    out = RecipeOut.model_validate(r)
    out.product_name = names.get(r.product_id)
    for ln in out.lines:
        ln.product_name = names.get(ln.product_id)
    return out


def _run_out(access: BusinessAccess, run: ProductionRun, names: dict[uuid.UUID, str]) -> RunOut:
    out = RunOut.model_validate(run)
    out.product_name = names.get(run.product_id)
    if not access.can("stock_value.view"):
        out.material_cost = out.overhead = out.total_cost = out.unit_cost = None
    return out


@router.get("/recipes", response_model=list[RecipeOut])
def list_recipes(access: View, db: DB) -> list[RecipeOut]:
    rows = db.scalars(select(Recipe).where(Recipe.business_id == access.business.id).order_by(Recipe.name))
    return [_recipe_out(db, r) for r in rows]


@router.post("/recipes", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
def create_recipe(body: RecipeIn, access: Manage, db: DB) -> RecipeOut:
    r = service.save_recipe(db, access, body.model_dump())
    db.commit()
    return _recipe_out(db, r)


@router.put("/recipes/{recipe_id}", response_model=RecipeOut)
def update_recipe(recipe_id: uuid.UUID, body: RecipeIn, access: Manage, db: DB) -> RecipeOut:
    r = service.save_recipe(db, access, body.model_dump(), service.get_recipe(db, access, recipe_id))
    db.commit()
    return _recipe_out(db, r)


@router.delete("/recipes/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recipe(recipe_id: uuid.UUID, access: Manage, db: DB) -> None:
    service.delete_recipe(db, access, service.get_recipe(db, access, recipe_id))
    db.commit()


@router.get("/recipes/{recipe_id}/plan")
def plan(recipe_id: uuid.UUID, access: View, db: DB, quantity: Decimal = Query(gt=0, max_digits=14, decimal_places=3),
         warehouse_id: uuid.UUID = Query(...)) -> dict[str, Any]:
    """Materials needed for a quantity, what the warehouse has, shortages and the estimated cost."""
    from app.modules.inventory.service import get_warehouse

    get_warehouse(db, access, warehouse_id)
    out = service.plan(db, access, service.get_recipe(db, access, recipe_id), quantity, warehouse_id)
    if not access.can("stock_value.view"):
        for k in ("material_cost", "overhead", "total_cost", "unit_cost"):
            out[k] = None
        for m in out["materials"]:
            m["unit_cost"] = m["estimated_cost"] = None
    return out


@router.get("/production", response_model=Page[RunOut])
def list_runs(access: View, db: DB, params: Annotated[PageParams, Depends(page_params)],
              qc_status: QcStatus | None = None) -> Page[RunOut]:
    from app.modules.inventory.service import visible_warehouse_ids

    stmt = select(ProductionRun).where(ProductionRun.business_id == access.business.id,
                                       ProductionRun.warehouse_id.in_(visible_warehouse_ids(db, access)))
    if qc_status:
        stmt = stmt.where(ProductionRun.qc_status == qc_status)
    rows, total = paginate(db, stmt.order_by(ProductionRun.created_at.desc()), params)
    names = _names(db, {r.product_id for r in rows})
    return Page(items=[_run_out(access, r, names) for r in rows], total=total, limit=params.limit, offset=params.offset)


@router.post("/production", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def produce(body: ProduceIn, access: Manage, db: DB) -> RunOut:
    """Record a finished production run: materials out, finished batch in, costs and books updated."""
    r = service.get_recipe(db, access, body.recipe_id)
    run = service.produce(db, access, r, quantity=body.quantity, warehouse_id=body.warehouse_id,
                          doc_date=body.doc_date or local_today(), batch_no=body.batch_no, expiry_date=body.expiry_date,
                          mrp=body.mrp, overhead=body.overhead, notes=body.notes)
    db.commit()
    return _run_out(access, run, _names(db, {run.product_id}))


@router.get("/production/{run_id}")
def get_run(run_id: uuid.UUID, access: View, db: DB) -> dict[str, Any]:
    from app.modules.inventory.service import document_entries, get_document

    run = service.get_run(db, access, run_id)
    doc = get_document(db, access, run.stock_document_id)
    entries = [e for e in document_entries(db, doc) if e.reverses_entry_id is None and e.quantity < 0]
    names = _names(db, {run.product_id, *(e.product_id for e in entries)})
    show = access.can("stock_value.view")
    batch_ids = [e.batch_id for e in entries if e.batch_id] or [uuid.uuid4()]
    batches = {b.id: b.batch_no for b in db.scalars(select(Batch).where(Batch.id.in_(batch_ids)))}
    return {**_run_out(access, run, names).model_dump(),
            "materials": [{"product_id": e.product_id, "name": names.get(e.product_id), "batch_no": batches.get(e.batch_id),
                           "quantity": -e.quantity, "value": -e.value if show else None} for e in entries]}


@router.post("/production/{run_id}/qc", response_model=RunOut)
def quality(run_id: uuid.UUID, body: QcIn, access: Manage, db: DB) -> RunOut:
    run = service.quality_decision(db, access, service.get_run(db, access, run_id), release=body.release, notes=body.notes)
    db.commit()
    return _run_out(access, run, _names(db, {run.product_id}))


@router.post("/production/{run_id}/cancel", response_model=RunOut)
def cancel(run_id: uuid.UUID, body: CancelIn, access: Manage, db: DB) -> RunOut:
    run = service.cancel(db, access, service.get_run(db, access, run_id), reason=body.reason, on=local_today())
    db.commit()
    return _run_out(access, run, _names(db, {run.product_id}))
