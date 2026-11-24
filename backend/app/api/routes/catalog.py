"""Units, categories, brands and products."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas_masters import (
    BrandIn,
    BrandOut,
    BrandUpdateIn,
    CategoryIn,
    CategoryOut,
    CategoryUpdateIn,
    ProductIn,
    ProductOut,
    ProductUpdateIn,
    UnitIn,
    UnitOut,
    UnitUpdateIn,
)
from app.modules.access.service import BusinessAccess
from app.modules.catalog import service
from app.modules.catalog.models import Product
from app.modules.inventory.models import StockBalance
from app.modules.inventory.service import visible_warehouse_ids

router = APIRouter(prefix="/businesses/{business_id}", tags=["products"])

ViewProducts = Annotated[BusinessAccess, Depends(require("products.view"))]
ManageProducts = Annotated[BusinessAccess, Depends(require("products.manage"))]


def product_out(db: Session, access: BusinessAccess, products: list[Product]) -> list[ProductOut]:
    """Serialize products, adding on-hand stock (if permitted) and hiding cost prices."""
    show_stock = access.can("stock.view")
    show_cost = access.can("stock_value.view")
    totals: dict[uuid.UUID, object] = {}
    if show_stock and products:
        wids = visible_warehouse_ids(db, access)
        totals = dict(db.execute(
            select(StockBalance.product_id, func.sum(StockBalance.quantity))
            .where(StockBalance.product_id.in_([p.id for p in products]),
                   StockBalance.warehouse_id.in_(wids))
            .group_by(StockBalance.product_id)
        ).all())
    out = []
    for p in products:
        item = ProductOut.model_validate(p)
        if not show_cost:
            item.purchase_price = None
        if show_stock and p.track_inventory:
            item.on_hand = totals.get(p.id) or Decimal(0)  # type: ignore[assignment]
        out.append(item)
    return out


# ---------------------------------------------------------------------------- units


@router.get("/units", response_model=list[UnitOut])
def list_units(access: ViewProducts, db: DB) -> list[UnitOut]:
    return [UnitOut.model_validate(u) for u in service.list_units(db, access)]


@router.post("/units", response_model=UnitOut, status_code=status.HTTP_201_CREATED)
def create_unit(body: UnitIn, access: ManageProducts, db: DB) -> UnitOut:
    u = service.create_unit(db, access, body.model_dump())
    db.commit()
    return UnitOut.model_validate(u)


@router.patch("/units/{unit_id}", response_model=UnitOut)
def update_unit(unit_id: uuid.UUID, body: UnitUpdateIn, access: ManageProducts, db: DB) -> UnitOut:
    u = service.update_unit(db, access, service.get_unit(db, access, unit_id),
                            body.model_dump(exclude_unset=True))
    db.commit()
    return UnitOut.model_validate(u)


# ---------------------------------------------------------------------------- categories


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(access: ViewProducts, db: DB) -> list[CategoryOut]:
    return [CategoryOut.model_validate(c) for c in service.list_categories(db, access)]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryIn, access: ManageProducts, db: DB) -> CategoryOut:
    c = service.create_category(db, access, body.model_dump())
    db.commit()
    return CategoryOut.model_validate(c)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: uuid.UUID, body: CategoryUpdateIn, access: ManageProducts,
                    db: DB) -> CategoryOut:
    c = service.update_category(db, access, service.get_category(db, access, category_id),
                                body.model_dump(exclude_unset=True))
    db.commit()
    return CategoryOut.model_validate(c)


# ---------------------------------------------------------------------------- brands


@router.get("/brands", response_model=list[BrandOut])
def list_brands(access: ViewProducts, db: DB) -> list[BrandOut]:
    return [BrandOut.model_validate(b) for b in service.list_brands(db, access)]


@router.post("/brands", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
def create_brand(body: BrandIn, access: ManageProducts, db: DB) -> BrandOut:
    b = service.create_brand(db, access, body.model_dump())
    db.commit()
    return BrandOut.model_validate(b)


@router.patch("/brands/{brand_id}", response_model=BrandOut)
def update_brand(brand_id: uuid.UUID, body: BrandUpdateIn, access: ManageProducts, db: DB) -> BrandOut:
    b = service.update_brand(db, access, service.get_brand(db, access, brand_id),
                             body.model_dump(exclude_unset=True))
    db.commit()
    return BrandOut.model_validate(b)


# ---------------------------------------------------------------------------- products


@router.get("/products", response_model=Page[ProductOut])
def list_products(
    access: ViewProducts, db: DB, params: Annotated[PageParams, Depends(page_params)],
    q: str | None = Query(None, max_length=100, description="Name, SKU, barcode or HSN"),
    category_id: uuid.UUID | None = None, brand_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> Page[ProductOut]:
    stmt = service.search_products(db, access, q=q, category_id=category_id, brand_id=brand_id,
                                   include_archived=include_archived)
    rows, total = paginate(db, stmt, params)
    return Page(items=product_out(db, access, rows), total=total, limit=params.limit,
                offset=params.offset)


@router.get("/products/lookup", response_model=ProductOut)
def lookup_product(access: ViewProducts, db: DB,
                   code: str = Query(min_length=1, max_length=64)) -> ProductOut:
    """Exact barcode/SKU lookup for scanners at the billing counter."""
    return product_out(db, access, [service.lookup_barcode(db, access, code)])[0]


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(body: ProductIn, access: ManageProducts, db: DB) -> ProductOut:
    p = service.create_product(db, access, body.model_dump())
    db.commit()
    return product_out(db, access, [p])[0]


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: uuid.UUID, access: ViewProducts, db: DB) -> ProductOut:
    return product_out(db, access, [service.get_product(db, access, product_id)])[0]


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: uuid.UUID, body: ProductUpdateIn, access: ManageProducts,
                   db: DB) -> ProductOut:
    p = service.update_product(db, access, service.get_product(db, access, product_id),
                               body.model_dump(exclude_unset=True))
    db.commit()
    return product_out(db, access, [p])[0]


@router.post("/products/{product_id}/archive", response_model=ProductOut)
def archive_product(product_id: uuid.UUID, access: ManageProducts, db: DB) -> ProductOut:
    p = service.archive_product(db, access, service.get_product(db, access, product_id))
    db.commit()
    return product_out(db, access, [p])[0]


@router.post("/products/{product_id}/restore", response_model=ProductOut)
def restore_product(product_id: uuid.UUID, access: ManageProducts, db: DB) -> ProductOut:
    p = service.restore_product(db, access, service.get_product(db, access, product_id))
    db.commit()
    return product_out(db, access, [p])[0]
