"""WhatsApp order catalogue: owner settings and the public (link-only) catalogue."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.deps import DB, require
from app.core.context import client_ip_var
from app.modules.access.service import BusinessAccess
from app.modules.shop import service

router = APIRouter(prefix="/businesses/{business_id}/catalogue", tags=["catalogue"])
public = APIRouter(prefix="/public/catalogue", tags=["public catalogue"])
Settings = Annotated[BusinessAccess, Depends(require("business.settings"))]
Token = Annotated[str, Path(min_length=32, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class CatalogueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] | None = None
    welcome: Annotated[str, StringConstraints(max_length=1000)] | None = None
    whatsapp_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=16)] | None = None
    category_ids: list[uuid.UUID] | None = Field(None, max_length=200)
    show_stock: bool | None = None
    min_order_value: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    branch_id: uuid.UUID | None = None
    is_active: bool | None = None


class OrderLine(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=1000)


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=16)]
    address: Annotated[str, StringConstraints(max_length=300)] | None = None
    note: Annotated[str, StringConstraints(max_length=500)] | None = None
    items: list[OrderLine] = Field(min_length=1, max_length=service.MAX_LINES)
    website: str | None = Field(None, max_length=100)  # honeypot


def _out(c: Any) -> dict[str, Any]:
    if c is None:
        return {"configured": False}
    return {"configured": True, "id": c.id, "token": c.token, "title": c.title, "welcome": c.welcome,
            "whatsapp_number": c.whatsapp_number, "category_ids": c.category_ids, "show_stock": c.show_stock,
            "min_order_value": c.min_order_value, "branch_id": c.branch_id, "is_active": c.is_active}


@router.get("")
def get_catalogue(access: Settings, db: DB) -> dict[str, Any]:
    out = _out(service.get_or_none(db, access.business.id))
    if out["configured"]:
        out["orders_24h"] = service.recent_count(db, access.business.id)
    return out


@router.put("")
def save_catalogue(body: CatalogueIn, access: Settings, db: DB) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if "category_ids" in data:
        data["category_ids"] = [str(x) for x in data["category_ids"] or []]
    c = service.save(db, access, data)
    db.commit()
    return _out(c)


@router.post("/rotate")
def rotate_link(access: Settings, db: DB) -> dict[str, Any]:
    """New secret link; the old one stops working at once."""
    c = service.rotate(db, access)
    db.commit()
    return _out(c)


@router.get("/orders")
def orders(access: Annotated[BusinessAccess, Depends(require("invoices.view"))], db: DB) -> list[dict[str, Any]]:
    return service.orders(db, access)


@public.get("/{token}")
def view(token: Token, db: DB) -> dict[str, Any]:
    """The catalogue a buyer sees (no login: the secret link is the key)."""
    return service.public_view(db, token)


@public.post("/{token}/orders", status_code=status.HTTP_201_CREATED)
def place(token: Token, body: OrderIn, db: DB) -> dict[str, Any]:
    out = service.place_order(db, token, body.model_dump(), client_ip_var.get())
    db.commit()
    return out


@router.get("/qr")
def qr(access: Settings, db: DB, origin: Annotated[str, Query(max_length=200)] = "") -> dict[str, str]:
    """A QR code (PNG data URL) of the catalogue link, for the counter or a poster."""
    from app.core.errors import NotFound
    from app.modules.billing.printing import _qr_data_url

    c = service.get_or_none(db, access.business.id)
    if c is None:
        raise NotFound("Set up the catalogue first")
    if not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        origin = ""
    url = f"{origin.rstrip('/')}/shop/{c.token}"
    return {"url": url, "qr": _qr_data_url(url)}
