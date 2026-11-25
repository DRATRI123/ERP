"""Units, categories, brands and products."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.catalog.models import Brand, Category, Product, ProductKind, Unit
from app.modules.events import service as events
from app.modules.inventory.models import StockBalance, StockLedgerEntry

PRODUCT_FIELDS = [
    "kind", "name", "sku", "barcode", "hsn_sac", "description", "category_id", "brand_id",
    "unit_id", "purchase_price", "selling_price", "wholesale_price", "mrp", "prices_include_tax",
    "gst_rate", "cess_rate", "track_inventory", "batch_tracking", "serial_tracking",
    "reorder_level", "reorder_quantity", "attributes", "is_active",
    "ptr", "pts", "composition", "rack", "drug_schedule", "pack_size", "pack_label", "warranty_months",
]
# Changing these after stock has moved would make the stock ledger meaningless.
_STOCK_LOCKED_FIELDS = {"unit_id", "track_inventory", "batch_tracking", "serial_tracking", "kind"}

# Units created with every business (GST UQC codes).
DEFAULT_UNITS = [
    ("NOS", "Numbers", "NOS", 0), ("PCS", "Pieces", "PCS", 0), ("BOX", "Box", "BOX", 0),
    ("BTL", "Bottle", "BTL", 0), ("PKT", "Packet", "PAC", 0), ("STRIP", "Strip", "OTH", 0),
    ("KG", "Kilogram", "KGS", 3), ("GM", "Gram", "GMS", 3), ("LTR", "Litre", "LTR", 3),
    ("ML", "Millilitre", "MLT", 3), ("MTR", "Metre", "MTR", 3), ("HRS", "Hours", "OTH", 2),
]


def _owned(obj: Any, access: BusinessAccess, what: str) -> Any:
    if obj is None or obj.business_id != access.business.id:
        raise NotFound(f"{what} not found")
    return obj


def _record(db: Session, access: BusinessAccess, action: str, entity: str, eid: Any,
            changes: dict | None = None) -> None:
    audit.record(db, action=action, entity_type=entity, entity_id=eid,
                 business_id=access.business.id, actor_id=access.user.id, changes=changes)


# ---------------------------------------------------------------------------- units


def seed_units(db: Session, business_id: uuid.UUID) -> None:
    for code, name, uqc, decimals in DEFAULT_UNITS:
        db.add(Unit(business_id=business_id, code=code, name=name, uqc=uqc, decimals=decimals))


def list_units(db: Session, access: BusinessAccess) -> list[Unit]:
    return list(db.scalars(select(Unit).where(Unit.business_id == access.business.id).order_by(Unit.code)))


def get_unit(db: Session, access: BusinessAccess, uid: uuid.UUID) -> Unit:
    return _owned(db.get(Unit, uid), access, "Unit")


def create_unit(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Unit:
    access.require("products.manage")
    if db.scalar(select(Unit.id).where(Unit.business_id == access.business.id, Unit.code == data["code"])):
        raise Conflict(f"Unit {data['code']} already exists")
    u = Unit(business_id=access.business.id, **data)
    db.add(u)
    db.flush()
    _record(db, access, "unit.created", "unit", u.id, {"code": u.code})
    return u


def update_unit(db: Session, access: BusinessAccess, u: Unit, data: dict[str, Any]) -> Unit:
    access.require("products.manage")
    if "decimals" in data and data["decimals"] < u.decimals:
        used = db.scalar(select(Product.id).where(Product.unit_id == u.id).limit(1))
        if used:
            raise ValidationFailed("Decimals cannot be reduced while products use this unit")
    before = audit.snapshot(u, ["name", "uqc", "decimals", "is_active"])
    for k, v in data.items():
        setattr(u, k, v)
    _record(db, access, "unit.updated", "unit", u.id,
            audit.diff(before, audit.snapshot(u, ["name", "uqc", "decimals", "is_active"])))
    return u


# ---------------------------------------------------------------------------- categories & brands


def list_categories(db: Session, access: BusinessAccess) -> list[Category]:
    return list(db.scalars(
        select(Category).where(Category.business_id == access.business.id).order_by(Category.name)))


def get_category(db: Session, access: BusinessAccess, cid: uuid.UUID) -> Category:
    return _owned(db.get(Category, cid), access, "Category")


def _check_category(db: Session, access: BusinessAccess, name: str, parent_id: uuid.UUID | None,
                    exclude: uuid.UUID | None = None) -> None:
    if parent_id is not None:
        get_category(db, access, parent_id)
    stmt = select(Category.id).where(
        Category.business_id == access.business.id,
        func.lower(Category.name) == name.lower(),
        Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id)
    if exclude:
        stmt = stmt.where(Category.id != exclude)
    if db.scalar(stmt):
        raise Conflict(f"Category {name} already exists here")


def create_category(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Category:
    access.require("products.manage")
    _check_category(db, access, data["name"], data.get("parent_id"))
    c = Category(business_id=access.business.id, **data)
    db.add(c)
    db.flush()
    _record(db, access, "category.created", "category", c.id, {"name": c.name})
    return c


def update_category(db: Session, access: BusinessAccess, c: Category, data: dict[str, Any]) -> Category:
    access.require("products.manage")
    parent_id = data.get("parent_id", c.parent_id)
    # Walk up from the new parent: reaching c itself would create a cycle.
    cursor = parent_id
    while cursor is not None:
        if cursor == c.id:
            raise ValidationFailed("A category cannot be placed inside itself")
        cursor = get_category(db, access, cursor).parent_id
    _check_category(db, access, data.get("name", c.name), parent_id, exclude=c.id)
    for k, v in data.items():
        setattr(c, k, v)
    _record(db, access, "category.updated", "category", c.id, {k: str(v) for k, v in data.items()})
    return c


def list_brands(db: Session, access: BusinessAccess) -> list[Brand]:
    return list(db.scalars(select(Brand).where(Brand.business_id == access.business.id).order_by(Brand.name)))


def get_brand(db: Session, access: BusinessAccess, bid: uuid.UUID) -> Brand:
    return _owned(db.get(Brand, bid), access, "Brand")


def create_brand(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Brand:
    access.require("products.manage")
    if db.scalar(select(Brand.id).where(Brand.business_id == access.business.id,
                                        func.lower(Brand.name) == data["name"].lower())):
        raise Conflict(f"Brand {data['name']} already exists")
    b = Brand(business_id=access.business.id, **data)
    db.add(b)
    db.flush()
    _record(db, access, "brand.created", "brand", b.id, {"name": b.name})
    return b


def update_brand(db: Session, access: BusinessAccess, b: Brand, data: dict[str, Any]) -> Brand:
    access.require("products.manage")
    if "name" in data and db.scalar(select(Brand.id).where(
            Brand.business_id == access.business.id, Brand.id != b.id,
            func.lower(Brand.name) == data["name"].lower())):
        raise Conflict(f"Brand {data['name']} already exists")
    for k, v in data.items():
        setattr(b, k, v)
    _record(db, access, "brand.updated", "brand", b.id, {k: str(v) for k, v in data.items()})
    return b


# ---------------------------------------------------------------------------- products


def get_product(db: Session, access: BusinessAccess, pid: uuid.UUID) -> Product:
    return _owned(db.get(Product, pid), access, "Product")


def search_products(db: Session, access: BusinessAccess, *, q: str | None, category_id: uuid.UUID | None,
                    brand_id: uuid.UUID | None, include_archived: bool):  # type: ignore[no-untyped-def]
    stmt = select(Product).where(Product.business_id == access.business.id)
    if not include_archived:
        stmt = stmt.where(Product.is_active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like),
                              func.lower(Product.composition).like(like),
                              Product.barcode == q, Product.hsn_sac == q))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if brand_id:
        stmt = stmt.where(Product.brand_id == brand_id)
    return stmt.order_by(Product.name, Product.sku)


def lookup_barcode(db: Session, access: BusinessAccess, code: str) -> Product:
    p = db.scalar(select(Product).where(
        Product.business_id == access.business.id, Product.is_active.is_(True),
        or_(Product.barcode == code, Product.sku == code)))
    if p is None:
        raise NotFound("No active product with this barcode or SKU")
    return p


def _validate_product(db: Session, access: BusinessAccess, data: dict[str, Any], current: Product | None) -> None:
    if "composition" in data:
        from app.modules.pharmacy.service import normalise_composition

        data["composition"] = normalise_composition(data["composition"])
    merged = {f: getattr(current, f) for f in PRODUCT_FIELDS} if current else {}
    merged.update(data)
    if merged.get("unit_id"):
        get_unit(db, access, merged["unit_id"])
    if merged.get("category_id"):
        get_category(db, access, merged["category_id"])
    if merged.get("brand_id"):
        get_brand(db, access, merged["brand_id"])
    if merged.get("kind") == ProductKind.SERVICE and (
            merged.get("track_inventory") or merged.get("batch_tracking") or merged.get("serial_tracking")):
        raise ValidationFailed("Services cannot track inventory, batches or serial numbers")
    if not merged.get("track_inventory") and (merged.get("batch_tracking") or merged.get("serial_tracking")):
        raise ValidationFailed("Batch and serial tracking require inventory tracking")
    hsn = merged.get("hsn_sac")
    if hsn and merged.get("kind") == ProductKind.SERVICE and not hsn.startswith("99"):
        raise ValidationFailed("SAC codes for services start with 99")
    if merged.get("pack_label") and not merged.get("pack_size"):
        raise ValidationFailed("Enter how many units a pack holds (pack size) to sell loose")
    if merged.get("pack_size") is not None and merged.get("kind") == ProductKind.SERVICE:
        raise ValidationFailed("Services are not sold in packs")
    mrp, sp = merged.get("mrp"), merged.get("selling_price")
    if mrp is not None and sp is not None and merged.get("prices_include_tax") and sp > mrp:
        raise ValidationFailed("Selling price cannot exceed MRP")
    # Scanning looks up barcode *or* SKU, so neither may collide with the other.
    for key in ("sku", "barcode"):
        val = data.get(key)
        if not val:
            continue
        stmt = select(Product.id).where(Product.business_id == access.business.id,
                                        or_(Product.sku == val, Product.barcode == val))
        if current:
            stmt = stmt.where(Product.id != current.id)
        if db.scalar(stmt):
            raise Conflict(f"Another product already uses {val} as its SKU or barcode")


def _payload(p: Product) -> dict[str, Any]:
    return {"id": p.id, **audit.snapshot(p, PRODUCT_FIELDS)}


def create_product(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Product:
    access.require("products.manage")
    if data.get("kind") == ProductKind.SERVICE:
        data["track_inventory"] = False
    _validate_product(db, access, data, None)
    p = Product(business_id=access.business.id, **data)
    db.add(p)
    db.flush()
    _record(db, access, "product.created", "product", p.id, audit.snapshot(p, PRODUCT_FIELDS))
    events.emit(db, "product.created", aggregate_type="product", aggregate_id=p.id,
                business_id=access.business.id, payload=_payload(p))
    return p


def has_stock_history(db: Session, product_id: uuid.UUID) -> bool:
    return db.scalar(select(StockLedgerEntry.id).where(
        StockLedgerEntry.product_id == product_id).limit(1)) is not None


def on_hand(db: Session, product_id: uuid.UUID) -> Any:
    return db.scalar(select(func.coalesce(func.sum(StockBalance.quantity), 0)).where(
        StockBalance.product_id == product_id))


def update_product(db: Session, access: BusinessAccess, p: Product, data: dict[str, Any]) -> Product:
    access.require("products.manage")
    locked = {k for k in data if k in _STOCK_LOCKED_FIELDS and data[k] != getattr(p, k)}
    if locked and has_stock_history(db, p.id):
        raise ValidationFailed("These fields cannot change after stock has moved",
                               details={"fields": sorted(locked)})
    _validate_product(db, access, data, p)
    before = audit.snapshot(p, PRODUCT_FIELDS)
    for k, v in data.items():
        setattr(p, k, v)
    changes = audit.diff(before, audit.snapshot(p, PRODUCT_FIELDS))
    if changes:
        _record(db, access, "product.updated", "product", p.id, changes)
        events.emit(db, "product.updated", aggregate_type="product", aggregate_id=p.id,
                    business_id=access.business.id, payload={"id": p.id, "changes": changes})
    return p


def archive_product(db: Session, access: BusinessAccess, p: Product) -> Product:
    """Products are never hard-deleted (documents reference them); archiving hides them."""
    access.require("products.manage")
    if not p.is_active:
        return p
    if on_hand(db, p.id):
        raise ValidationFailed("Adjust the remaining stock to zero before archiving this product")
    p.is_active = False
    p.archived_at = datetime.now(UTC)
    _record(db, access, "product.archived", "product", p.id)
    events.emit(db, "product.deleted", aggregate_type="product", aggregate_id=p.id,
                business_id=access.business.id, payload={"id": p.id, "sku": p.sku})
    return p


def restore_product(db: Session, access: BusinessAccess, p: Product) -> Product:
    access.require("products.manage")
    if p.is_active:
        return p
    p.is_active = True
    p.archived_at = None
    _record(db, access, "product.restored", "product", p.id)
    events.emit(db, "product.updated", aggregate_type="product", aggregate_id=p.id,
                business_id=access.business.id, payload={"id": p.id, "changes": {"is_active": True}})
    return p
