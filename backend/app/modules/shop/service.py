"""Public catalogue for ordering on WhatsApp.

The owner turns on a catalogue and shares its link (on WhatsApp, a QR code at the counter…).
Buyers open it on their phone, pick products and quantities and send the order:

* the ERP records it as a **draft sales order** for the business (prices from the product, or the
  buyer's price list when the phone number matches a customer), and remembers who sent it;
* the buyer's phone then opens WhatsApp with the order summary addressed to the business, so
  the conversation continues where the buyer already is.

Safety: the link holds a 32-byte random token (rotate it to revoke old links); only active
products of the chosen categories with a selling price are shown; stock shows as in/out only;
orders are limited per IP and per phone, items and quantities are capped, and a hidden field
catches form-filling bots. Nothing is sold until a staff member reviews and converts the order.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import NotFound, TooManyRequests, ValidationFailed
from app.core.ratelimit import limiter
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.businesses.models import Business, BusinessStatus
from app.modules.catalog.models import Category, Product
from app.modules.shop.models import Catalogue, CatalogueOrder

PHONE = re.compile(r"^(?:\+?91)?([6-9][0-9]{9})$")
MAX_LINES, MAX_QTY = 50, Decimal(1000)


def _photo(p: Any) -> str | None:
    from app.modules.catalog import images

    first = images.ids(p)[:1]
    return images.url(first[0], thumb=True) if first else None


def _phone(raw: str) -> str:
    m = PHONE.match(re.sub(r"[\s\-()]", "", raw or ""))
    if not m:
        raise ValidationFailed("Enter a 10-digit Indian mobile number")
    return m.group(1)


# ---------------------------------------------------------------------------- owner side


def get_or_none(db: Session, business_id: uuid.UUID) -> Catalogue | None:
    return db.scalar(select(Catalogue).where(Catalogue.business_id == business_id))


def save(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Catalogue:
    access.require("business.settings")
    c = get_or_none(db, access.business.id)
    if c is None:
        c = Catalogue(business_id=access.business.id, token=secrets.token_urlsafe(32), title=access.business.name, attributes={})
        db.add(c)
    for k in ("title", "welcome", "category_ids", "show_stock", "min_order_value", "branch_id", "is_active"):
        if k in data:
            setattr(c, k, data[k])
    if "whatsapp_number" in data:
        c.whatsapp_number = ("91" + _phone(data["whatsapp_number"])) if data["whatsapp_number"] else None
    for cid in c.category_ids or []:
        cat = db.get(Category, uuid.UUID(str(cid)))
        if cat is None or cat.business_id != access.business.id:
            raise ValidationFailed("Category not found")
    c.category_ids = [str(x) for x in c.category_ids or []]
    db.flush()
    audit.record(db, action="catalogue.saved", entity_type="catalogue", entity_id=c.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"is_active": c.is_active, "categories": len(c.category_ids)})
    return c


def rotate(db: Session, access: BusinessAccess) -> Catalogue:
    access.require("business.settings")
    c = get_or_none(db, access.business.id)
    if c is None:
        raise NotFound("Set up the catalogue first")
    c.token = secrets.token_urlsafe(32)
    db.flush()
    audit.record(db, action="catalogue.link_rotated", entity_type="catalogue", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id)
    return c


def orders(db: Session, access: BusinessAccess, limit: int = 50) -> list[dict[str, Any]]:
    from app.modules.billing.models import Document

    access.require("invoices.view")
    rows = db.execute(select(CatalogueOrder, Document).join(Document, Document.id == CatalogueOrder.document_id)
                      .where(CatalogueOrder.business_id == access.business.id)
                      .order_by(CatalogueOrder.created_at.desc()).limit(limit)).all()
    return [{"id": o.id, "document_id": d.id, "status": d.status.value, "number": d.number, "grand_total": d.grand_total,
             "name": o.name, "phone": o.phone, "address": o.address, "note": o.note, "created_at": o.created_at,
             "converted": d.status.value == "final"} for o, d in rows]


# ---------------------------------------------------------------------------- public side


def _public(db: Session, token: str) -> tuple[Catalogue, Business]:
    c = db.scalar(select(Catalogue).where(Catalogue.token == token, Catalogue.is_active.is_(True))) if len(token) >= 32 else None
    biz = db.get(Business, c.business_id) if c else None
    if c is None or biz is None or biz.status != BusinessStatus.ACTIVE:
        raise NotFound("This catalogue is not available")
    return c, biz


def _products(db: Session, c: Catalogue) -> list[Product]:
    stmt = select(Product).where(Product.business_id == c.business_id, Product.is_active.is_(True),
                                 Product.selling_price.is_not(None), Product.selling_price > 0)
    if c.category_ids:
        stmt = stmt.where(Product.category_id.in_([uuid.UUID(x) for x in c.category_ids]))
    return list(db.scalars(stmt.order_by(Product.name).limit(1000)))


def public_view(db: Session, token: str) -> dict[str, Any]:
    from app.modules.inventory.models import StockBalance

    c, biz = _public(db, token)
    products = _products(db, c)
    stock: dict[uuid.UUID, Decimal] = {}
    if c.show_stock and products:
        stock = dict(db.execute(select(StockBalance.product_id, func.sum(StockBalance.quantity))
                                .where(StockBalance.product_id.in_([p.id for p in products]))
                                .group_by(StockBalance.product_id)).all())
    cats = {x.id: x.name for x in db.scalars(select(Category).where(Category.business_id == c.business_id))}
    return {
        "business": biz.name, "title": c.title, "welcome": c.welcome, "whatsapp": bool(c.whatsapp_number),
        "min_order_value": c.min_order_value, "phone": biz.phone,
        "products": [{"id": p.id, "name": p.name, "sku": p.sku, "price": p.selling_price, "mrp": p.mrp,
                      "image": _photo(p), "description": p.description,
                      "prices_include_tax": p.prices_include_tax, "gst_rate": p.gst_rate,
                      "category": cats.get(p.category_id) if p.category_id else None,
                      "in_stock": (not p.track_inventory) or (stock.get(p.id) or 0) > 0 if c.show_stock else None}
                     for p in products],
    }


def place_order(db: Session, token: str, data: dict[str, Any], ip: str | None) -> dict[str, Any]:
    from app.modules.billing.doctypes import DocType
    from app.modules.billing.service import LineIn, create_draft
    from app.modules.integrations.sync import system_access
    from app.modules.parties.models import Party

    if data.get("website"):  # hidden field: people never fill it in, bots do
        raise ValidationFailed("Could not place the order")
    c, biz = _public(db, token)
    phone = _phone(data.get("phone", ""))
    for key, limit, window in ((f"shop-ip:{ip}", 10, 3600.0), (f"shop-phone:{c.business_id}:{phone}", 5, 3600.0)):
        allowed, _, retry = limiter.hit(key, limit, window=window)
        if not allowed:
            raise TooManyRequests("Too many orders in a short time. Please message us on WhatsApp.",
                                  code="order_rate_limited", headers={"Retry-After": str(retry)})
    name = (data.get("name") or "").strip()[:120]
    if len(name) < 2:
        raise ValidationFailed("Enter your name")
    items = data.get("items") or []
    if not items or len(items) > MAX_LINES:
        raise ValidationFailed(f"Choose 1 to {MAX_LINES} products")
    allowed_ids = {p.id: p for p in _products(db, c)}
    lines = []
    for it in items:
        pid, qty = uuid.UUID(str(it["product_id"])), Decimal(str(it["quantity"]))
        if pid not in allowed_ids:
            raise ValidationFailed("A product in the order is no longer available")
        if not 0 < qty <= MAX_QTY or qty != qty.to_integral_value():
            raise ValidationFailed("Quantities must be whole numbers from 1 to 1000")
        lines.append(LineIn(product_id=pid, quantity=qty))

    access = system_access(db, c.business_id)
    party = db.scalar(select(Party).where(Party.business_id == c.business_id, Party.is_customer.is_(True),
                                          Party.phone.in_([phone, f"+91{phone}", f"91{phone}"])))
    note = (data.get("note") or "").strip()[:500] or None
    address = (data.get("address") or "").strip()[:300] or None
    header = {"doc_date": local_today(), "branch_id": c.branch_id,
              "notes": "\n".join(x for x in (f"Online order from {name} ({phone})", address and f"Deliver to: {address}", note) if x)}
    if party is not None:
        header["party_id"] = party.id
    else:
        header["party_name"] = name
    doc = create_draft(db, access, DocType.SALES_ORDER, header, lines)
    if doc.grand_total < c.min_order_value:
        raise ValidationFailed(f"The minimum order is ₹{c.min_order_value:,.0f}")
    order = CatalogueOrder(business_id=c.business_id, document_id=doc.id, party_id=party.id if party else None, name=name,
                           phone=phone, address=address, note=note, ip=ip)
    db.add(order)
    db.flush()
    from app.modules.events import service as events

    events.emit(db, "order.received", aggregate_type="document", aggregate_id=doc.id, business_id=c.business_id,
                payload={"id": doc.id, "name": name, "phone": phone, "grand_total": doc.grand_total, "lines": len(doc.lines)})
    ref = f"WEB-{str(order.id)[:8].upper()}"
    summary = "\n".join(f"• {ln.description} × {ln.quantity.normalize():f}" for ln in doc.lines)
    text = f"Hello {biz.name}, I placed order {ref} on your catalogue:\n{summary}\nTotal ₹{doc.grand_total:,.2f}\n— {name}"
    return {"reference": ref, "grand_total": doc.grand_total, "lines": len(doc.lines),
            "whatsapp_url": f"https://wa.me/{c.whatsapp_number}?text={quote(text)}" if c.whatsapp_number else None}


def recent_count(db: Session, business_id: uuid.UUID, hours: int = 24) -> int:
    since = datetime.now(UTC) - timedelta(hours=hours)
    return int(db.scalar(select(func.count()).select_from(CatalogueOrder).where(
        CatalogueOrder.business_id == business_id, CatalogueOrder.created_at >= since)) or 0)
