"""Price lists, schemes and the pricing hooks used by billing.

* A customer may have a **price list**. Sales lines without a typed price take the list's
  fixed price for the product (the highest quantity break the line reaches), otherwise the
  list's base price (selling, wholesale, MRP or purchase) adjusted by its percentage.
* **Schemes** apply automatically on sales documents: *discount* schemes set the line
  discount (when the user typed none); *free goods* schemes add a free line (same product,
  100 % discount) for every multiple of the minimum quantity — "10 + 1".
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.catalog.models import Category, Product
from app.modules.pricing.models import PriceBase, PriceList, PriceListItem, Scheme, SchemeKind

ZERO = Decimal(0)
HUNDRED = Decimal(100)
FREE_FLAG = "scheme_free"


# ---------------------------------------------------------------------------- price lists


def _base_price(product: Product, base: PriceBase) -> Decimal | None:
    return {PriceBase.SELLING: product.selling_price, PriceBase.WHOLESALE: product.wholesale_price or product.selling_price,
            PriceBase.MRP: product.mrp, PriceBase.PURCHASE: product.purchase_price}[base]


def list_price(db: Session, price_list_id: uuid.UUID, product: Product, quantity: Decimal) -> tuple[Decimal, bool | None] | None:
    """(price, prices_include_tax) for a product on a price list, or None if it cannot price it."""
    pl = db.get(PriceList, price_list_id)
    if pl is None or not pl.is_active:
        return None
    items = [i for i in pl.items if i.product_id == product.id and i.min_quantity <= quantity]
    if items:
        return max(items, key=lambda i: i.min_quantity).price, pl.prices_include_tax
    base = _base_price(product, pl.base)
    if base is None:
        return None
    price = (base * (HUNDRED + pl.adjust_percent) / HUNDRED).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    inclusive = pl.prices_include_tax if pl.prices_include_tax is not None else (True if pl.base == PriceBase.MRP else None)
    return price, inclusive


def _check_products(db: Session, access: BusinessAccess, ids: set[uuid.UUID]) -> None:
    for pid in ids:
        p = db.get(Product, pid)
        if p is None or p.business_id != access.business.id:
            raise ValidationFailed("Product not found")


def save_price_list(db: Session, access: BusinessAccess, data: dict[str, Any], pl: PriceList | None = None) -> PriceList:
    items = data.pop("items", None)
    name = data.get("name", pl.name if pl else None)
    dup = select(PriceList.id).where(PriceList.business_id == access.business.id, PriceList.name == name)
    if pl is not None:
        dup = dup.where(PriceList.id != pl.id)
    if db.scalar(dup):
        raise ValidationFailed(f"A price list named {name} already exists")
    if pl is None:
        pl = PriceList(business_id=access.business.id)
        db.add(pl)
    for k, v in data.items():
        setattr(pl, k, v)
    if not -100 < (pl.adjust_percent or ZERO) <= 1000:
        raise ValidationFailed("Adjustment must be between −100 % and 1000 %")
    if items is not None:
        keys = [(i["product_id"], Decimal(i.get("min_quantity") or 1)) for i in items]
        if len(set(keys)) != len(keys):
            raise ValidationFailed("A product appears twice with the same minimum quantity")
        _check_products(db, access, {i["product_id"] for i in items})
        pl.items.clear()
        db.flush()
        pl.items.extend(PriceListItem(product_id=i["product_id"], min_quantity=Decimal(i.get("min_quantity") or 1),
                                      price=i["price"]) for i in items)
    db.flush()
    audit.record(db, action="price_list.saved", entity_type="price_list", entity_id=pl.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"name": pl.name, "base": pl.base.value, "adjust_percent": pl.adjust_percent,
                          "items": len(pl.items)})
    return pl


def get_price_list(db: Session, access: BusinessAccess, pid: uuid.UUID) -> PriceList:
    pl = db.get(PriceList, pid)
    if pl is None or pl.business_id != access.business.id:
        raise NotFound("Price list not found")
    return pl


def delete_price_list(db: Session, access: BusinessAccess, pl: PriceList) -> None:
    from app.modules.parties.models import Party

    used = db.scalar(select(Party.id).where(Party.price_list_id == pl.id).limit(1))
    if used:
        raise ValidationFailed("Customers still use this price list; move them first or switch it off")
    audit.record(db, action="price_list.deleted", entity_type="price_list", entity_id=pl.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"name": pl.name})
    db.delete(pl)
    db.flush()


# ---------------------------------------------------------------------------- schemes


def save_scheme(db: Session, access: BusinessAccess, data: dict[str, Any], s: Scheme | None = None) -> Scheme:
    if s is None:
        s = Scheme(business_id=access.business.id)
        db.add(s)
    for k, v in data.items():
        setattr(s, k, v)
    if (s.product_id is None) == (s.category_id is None):
        raise ValidationFailed("Choose either one product or one category")
    if s.product_id:
        _check_products(db, access, {s.product_id})
    if s.category_id:
        c = db.get(Category, s.category_id)
        if c is None or c.business_id != access.business.id:
            raise ValidationFailed("Category not found")
    if s.kind == SchemeKind.FREE_GOODS and not (s.free_quantity and s.free_quantity > 0):
        raise ValidationFailed("Give the free quantity (e.g. 1 for 10 + 1)")
    if s.kind == SchemeKind.DISCOUNT and not (ZERO < (s.discount_percent or ZERO) <= HUNDRED):
        raise ValidationFailed("Give a discount between 0 and 100 %")
    if s.valid_from and s.valid_to and s.valid_to < s.valid_from:
        raise ValidationFailed("The scheme ends before it starts")
    if s.claim_party_id:
        from app.modules.parties.models import Party

        party = db.get(Party, s.claim_party_id)
        if party is None or party.business_id != access.business.id:
            raise ValidationFailed("Claim-from party not found")
    db.flush()
    audit.record(db, action="scheme.saved", entity_type="scheme", entity_id=s.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": s.name, "kind": s.kind.value})
    return s


def get_scheme(db: Session, access: BusinessAccess, sid: uuid.UUID) -> Scheme:
    s = db.get(Scheme, sid)
    if s is None or s.business_id != access.business.id:
        raise NotFound("Scheme not found")
    return s


def scheme_for(db: Session, business_id: uuid.UUID, product: Product, quantity: Decimal, on: date) -> Scheme | None:
    """The scheme a line qualifies for: product schemes before category ones, the highest slab first."""
    rows = db.scalars(select(Scheme).where(
        Scheme.business_id == business_id, Scheme.is_active.is_(True), Scheme.min_quantity <= quantity,
        or_(Scheme.product_id == product.id,
            Scheme.category_id == product.category_id if product.category_id else Scheme.id.is_(None)),
        or_(Scheme.valid_from.is_(None), Scheme.valid_from <= on), or_(Scheme.valid_to.is_(None), Scheme.valid_to >= on),
    )).all()
    if not rows:
        return None
    return sorted(rows, key=lambda s: (s.product_id is None, -s.min_quantity))[0]


def apply_schemes(db: Session, access: BusinessAccess, doc: Any) -> None:
    """Sales documents: set scheme discounts and (re)build free-goods lines."""
    from app.modules.billing.models import DocumentLine

    for ln in [ln for ln in doc.lines if (ln.custom_fields or {}).get(FREE_FLAG)]:
        doc.lines.remove(ln)
    free_lines = []
    for ln in list(doc.lines):
        if ln.product_id is None or ln.source_line_id is not None or (ln.custom_fields or {}).get("free"):
            continue
        product = db.get(Product, ln.product_id)
        s = scheme_for(db, access.business.id, product, ln.quantity, doc.doc_date) if product else None
        if s is None:
            if (ln.custom_fields or {}).get("scheme_id"):  # no longer qualifies: drop the scheme discount
                ln.discount_percent = ZERO
                ln.custom_fields = {k: v for k, v in ln.custom_fields.items() if k not in ("scheme_id", "scheme")}
            continue
        if s.kind == SchemeKind.DISCOUNT:
            if (ln.discount_percent == 0 and ln.discount_amount == 0) or (ln.custom_fields or {}).get("scheme_id"):
                ln.discount_percent = s.discount_percent
                ln.custom_fields = {**(ln.custom_fields or {}), "scheme_id": str(s.id), "scheme": s.name}
            continue
        free = (ln.quantity / s.min_quantity).to_integral_value(rounding=ROUND_DOWN) * s.free_quantity
        if free <= 0:
            continue
        skip = {"id", "document_id", "line_no", "quantity", "discount_percent", "discount_amount", "custom_fields",
                "batch_no", "expiry_date", "serials", "description"}
        cols = {c.key: getattr(ln, c.key) for c in DocumentLine.__mapper__.column_attrs if c.key not in skip}
        free_lines.append(DocumentLine(**cols, line_no=0, quantity=free, discount_percent=HUNDRED, discount_amount=ZERO,
                                       custom_fields={FREE_FLAG: True, "scheme_id": str(s.id), "scheme": s.name},
                                       description=f"{ln.description} — FREE ({s.name})"[:500]))
    n = max((ln.line_no for ln in doc.lines), default=0)
    for i, fl in enumerate(free_lines, 1):
        fl.line_no = n + i
        doc.lines.append(fl)
