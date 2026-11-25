"""Loyalty points: customers earn points on sales invoices and spend them as a payment mode.

Settings (``business.settings["loyalty"]``): ``earn_per_100`` points per ₹100 billed, ``point_value``
in ₹ per point, ``min_redeem`` points before they can be used. Earned points are reversed when the
invoice is cancelled; redeemed points come back when that receipt is cancelled. A redemption is
booked as Dr Loyalty Rewards (expense), Cr Receivable.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.businesses.models import Business
from app.modules.businesses.modules import is_enabled
from app.modules.parties.models import Party
from app.modules.stockx.models import LoyaltyEntry, LoyaltyKind

ZERO = Decimal(0)
P2 = Decimal("0.01")


def settings_of(business: Business) -> dict[str, Decimal]:
    s = (business.settings or {}).get("loyalty") or {}
    return {"earn_per_100": Decimal(str(s.get("earn_per_100") or 0)), "point_value": Decimal(str(s.get("point_value") or 0)),
            "min_redeem": Decimal(str(s.get("min_redeem") or 0))}


def active(business: Business) -> bool:
    cfg = settings_of(business)
    return is_enabled(business.settings, "loyalty") and cfg["earn_per_100"] > 0 and cfg["point_value"] > 0


def balance(db: Session, business_id: uuid.UUID, party_id: uuid.UUID) -> Decimal:
    return Decimal(db.scalar(select(func.coalesce(func.sum(LoyaltyEntry.points), 0)).where(
        LoyaltyEntry.business_id == business_id, LoyaltyEntry.party_id == party_id)) or 0)


def _add(db: Session, business_id: uuid.UUID, party_id: uuid.UUID, kind: LoyaltyKind, points: Decimal, on: date, *,
         document_id: uuid.UUID | None = None, payment_id: uuid.UUID | None = None, note: str | None = None,
         user_id: uuid.UUID | None = None) -> LoyaltyEntry | None:
    points = points.quantize(P2)
    if points == 0:
        return None
    e = LoyaltyEntry(business_id=business_id, party_id=party_id, kind=kind, points=points, entry_date=on,
                     document_id=document_id, payment_id=payment_id, note=note, created_by_id=user_id)
    db.add(e)
    db.flush()
    return e


# ---------------------------------------------------------------------------- hooks from billing


def earn_for_invoice(db: Session, business: Business, doc: Any, *, redeemed_value: Decimal = ZERO,
                     user_id: uuid.UUID | None = None) -> None:
    """On a finalized sales invoice with a customer: points on the value not paid with points."""
    if doc.party_id is None or not active(business):
        return
    cfg = settings_of(business)
    base = max(ZERO, doc.grand_total - redeemed_value)
    points = (base * cfg["earn_per_100"] / 100).quantize(P2, rounding=ROUND_DOWN)
    _add(db, business.id, doc.party_id, LoyaltyKind.EARN, points, doc.doc_date, document_id=doc.id,
         note=f"On {doc.number}", user_id=user_id)


def reverse_for_invoice(db: Session, business: Business, doc: Any, on: date, user_id: uuid.UUID | None = None) -> None:
    earned = Decimal(db.scalar(select(func.coalesce(func.sum(LoyaltyEntry.points), 0)).where(
        LoyaltyEntry.business_id == business.id, LoyaltyEntry.document_id == doc.id)) or 0)
    if earned and doc.party_id:
        _add(db, business.id, doc.party_id, LoyaltyKind.REVERSE, -earned, on, document_id=doc.id,
             note=f"{doc.number} cancelled", user_id=user_id)


def redeem(db: Session, business: Business, *, party_id: uuid.UUID | None, amount: Decimal, payment_id: uuid.UUID,
           on: date, number: str, user_id: uuid.UUID | None = None) -> None:
    """A receipt in mode "loyalty": spend amount / point_value points."""
    if party_id is None:
        raise ValidationFailed("Points need a customer")
    if not active(business):
        raise ValidationFailed("Loyalty points are not set up for this business (Loyalty page)")
    cfg = settings_of(business)
    points = (amount / cfg["point_value"]).quantize(P2, rounding=ROUND_HALF_UP)
    have = balance(db, business.id, party_id)
    if points > have:
        raise ValidationFailed(f"Only {have.normalize():f} points available (worth ₹{(have * cfg['point_value']).quantize(P2)})")
    if cfg["min_redeem"] and have < cfg["min_redeem"]:
        raise ValidationFailed(f"Points can be used from {cfg['min_redeem'].normalize():f} points")
    _add(db, business.id, party_id, LoyaltyKind.REDEEM, -points, on, payment_id=payment_id, note=f"Used on {number}",
         user_id=user_id)


def unredeem(db: Session, business: Business, payment: Any, on: date, user_id: uuid.UUID | None = None) -> None:
    used = Decimal(db.scalar(select(func.coalesce(func.sum(LoyaltyEntry.points), 0)).where(
        LoyaltyEntry.business_id == business.id, LoyaltyEntry.payment_id == payment.id)) or 0)
    if used and payment.party_id:
        _add(db, business.id, payment.party_id, LoyaltyKind.REVERSE, -used, on, payment_id=payment.id,
             note=f"{payment.number} cancelled", user_id=user_id)


# ---------------------------------------------------------------------------- API helpers


def statement(db: Session, access: BusinessAccess, party_id: uuid.UUID) -> dict[str, Any]:
    access.require("customers.view")
    party = db.get(Party, party_id)
    if party is None or party.business_id != access.business.id:
        raise ValidationFailed("Customer not found")
    cfg = settings_of(access.business)
    bal = balance(db, access.business.id, party_id)
    rows = db.scalars(select(LoyaltyEntry).where(LoyaltyEntry.business_id == access.business.id,
                                                 LoyaltyEntry.party_id == party_id)
                      .order_by(LoyaltyEntry.entry_date.desc(), LoyaltyEntry.created_at.desc()).limit(200))
    return {"party_id": party_id, "points": bal, "value": (bal * cfg["point_value"]).quantize(P2),
            "can_redeem": active(access.business) and bal > 0 and (not cfg["min_redeem"] or bal >= cfg["min_redeem"]),
            "entries": [{"id": e.id, "date": e.entry_date, "kind": e.kind.value, "points": e.points, "note": e.note,
                         "document_id": e.document_id, "payment_id": e.payment_id} for e in rows]}


def adjust(db: Session, access: BusinessAccess, party_id: uuid.UUID, points: Decimal, note: str, on: date) -> dict[str, Any]:
    access.require("customers.manage")
    party = db.get(Party, party_id)
    if party is None or party.business_id != access.business.id:
        raise ValidationFailed("Customer not found")
    if points < 0 and balance(db, access.business.id, party_id) + points < 0:
        raise ValidationFailed("That would make the balance negative")
    _add(db, access.business.id, party_id, LoyaltyKind.ADJUST, points, on, note=note, user_id=access.user.id)
    audit.record(db, action="loyalty.adjusted", entity_type="party", entity_id=party_id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"points": points, "note": note})
    return statement(db, access, party_id)


def members(db: Session, access: BusinessAccess, *, limit: int = 100) -> dict[str, Any]:
    access.require("customers.view")
    cfg = settings_of(access.business)
    rows = db.execute(select(Party.id, Party.name, Party.phone, func.sum(LoyaltyEntry.points).label("pts"))
                      .join(Party, Party.id == LoyaltyEntry.party_id)
                      .where(LoyaltyEntry.business_id == access.business.id)
                      .group_by(Party.id, Party.name, Party.phone)
                      .order_by(func.sum(LoyaltyEntry.points).desc()).limit(limit)).all()
    total = Decimal(db.scalar(select(func.coalesce(func.sum(LoyaltyEntry.points), 0)).where(
        LoyaltyEntry.business_id == access.business.id)) or 0)
    return {"settings": cfg, "active": active(access.business), "outstanding_points": total,
            "outstanding_value": (total * cfg["point_value"]).quantize(P2),
            "members": [{"party_id": i, "name": n, "phone": ph, "points": Decimal(p),
                         "value": (Decimal(p) * cfg["point_value"]).quantize(P2)} for i, n, ph, p in rows if p]}
