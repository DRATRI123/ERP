"""Damaged / broken stock: write it off and claim it from the supplier.

Recording damage posts a stock adjustment (Dr Stock adjustments, Cr Inventory at cost). When the
supplier settles the claim with a credit note, the amount is taken off what we owe them:
Dr Accounts payable (supplier), Cr Stock adjustments — so the loss shrinks by the recovery.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.chart import Accounts
from app.modules.accounting.ledger import Line, post
from app.modules.accounting.models import JournalSource
from app.modules.audit import service as audit
from app.modules.catalog.models import Product
from app.modules.inventory import service as inventory
from app.modules.inventory.models import AdjustmentReason
from app.modules.numbering.service import next_number
from app.modules.parties.models import Party
from app.modules.stockx.models import ClaimStatus, DamageClaim, DamageReason

ZERO = Decimal(0)


def q2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def create_claim(db: Session, access: BusinessAccess, data: dict[str, Any]) -> DamageClaim:
    access.require("stock.edit")
    product = db.get(Product, data["product_id"])
    if product is None or product.business_id != access.business.id:
        raise ValidationFailed("Product not found")
    party_id = data.get("party_id")
    if party_id:
        party = db.get(Party, party_id)
        if party is None or party.business_id != access.business.id:
            raise ValidationFailed("Supplier not found")
    reason = DamageReason(data["reason"])
    on: date = data["claim_date"]
    fy_code = inventory._open_fy(db, access.business.id, on).code
    number = next_number(db, access.business.id, f"damage_claim/{fy_code}", prefix=f"DMG/{fy_code}/")
    doc = inventory.create_adjustment(
        db, access, warehouse_id=data["warehouse_id"], doc_date=on,
        reason=AdjustmentReason.EXPIRY if reason == DamageReason.EXPIRY else AdjustmentReason.DAMAGE,
        lines=[inventory.LineIn(product_id=product.id, quantity=-Decimal(data["quantity"]), batch_no=data.get("batch_no"))],
        reference=number, notes=f"{reason.value.title()}: {data.get('notes') or product.name}"[:500])
    cost = q2(-sum((e.value for e in inventory.document_entries(db, doc)), ZERO))
    claim_amount = q2(Decimal(data["claim_amount"])) if data.get("claim_amount") is not None else cost
    c = DamageClaim(business_id=access.business.id, number=number, claim_date=on, product_id=product.id,
                    warehouse_id=data["warehouse_id"], batch_no=data.get("batch_no"), quantity=Decimal(data["quantity"]),
                    reason=reason, cost_value=cost, party_id=party_id, claim_amount=claim_amount if party_id else ZERO,
                    settled_amount=ZERO, status=ClaimStatus.OPEN if party_id else ClaimStatus.WRITTEN_OFF,
                    stock_document_id=doc.id, notes=data.get("notes"), file_id=data.get("file_id"),
                    created_by_id=access.user.id)
    db.add(c)
    db.flush()
    audit.record(db, action="damage_claim.created", entity_type="damage_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": number, "product": product.name, "quantity": c.quantity, "cost": cost,
                          "claim_amount": c.claim_amount})
    return c


def get_claim(db: Session, access: BusinessAccess, claim_id: uuid.UUID) -> DamageClaim:
    c = db.get(DamageClaim, claim_id)
    if c is None or c.business_id != access.business.id:
        raise NotFound("Claim not found")
    return c


def list_claims(db: Session, access: BusinessAccess, *, status: ClaimStatus | None = None) -> list[DamageClaim]:
    access.require("stock.view")
    stmt = select(DamageClaim).where(DamageClaim.business_id == access.business.id)
    if status:
        stmt = stmt.where(DamageClaim.status == status)
    return list(db.scalars(stmt.order_by(DamageClaim.claim_date.desc(), DamageClaim.number.desc()).limit(500)))


def summary(db: Session, access: BusinessAccess) -> dict[str, Any]:
    access.require("stock.view")
    out: dict[str, Any] = {}
    for st in ClaimStatus:
        n, cost, claim, settled = db.execute(select(
            func.count(DamageClaim.id), func.coalesce(func.sum(DamageClaim.cost_value), 0),
            func.coalesce(func.sum(DamageClaim.claim_amount), 0), func.coalesce(func.sum(DamageClaim.settled_amount), 0))
            .where(DamageClaim.business_id == access.business.id, DamageClaim.status == st)).one()
        out[st.value] = {"count": int(n), "cost": Decimal(cost), "claimed": Decimal(claim), "settled": Decimal(settled)}
    return out


def settle_claim(db: Session, access: BusinessAccess, c: DamageClaim, *, amount: Decimal, on: date,
                 reference: str | None) -> DamageClaim:
    access.require("stock.edit", "purchases.edit")
    if c.status != ClaimStatus.OPEN or c.party_id is None:
        raise Conflict(f"This claim is {c.status.value.replace('_', ' ')}")
    amount = q2(amount)
    if amount <= 0:
        raise ValidationFailed("Enter the amount the supplier credited")
    acc = Accounts(db, access.business.id)
    post(db, access.business.id, entry_date=on, source_type=JournalSource.CLAIM, source_id=c.id,
         source_ref=reference or c.number, created_by=access.user.id,
         narration=f"Damage claim {c.number} settled" + (f" (credit note {reference})" if reference else ""),
         lines=[Line(acc["ap"], debit=amount, party_id=c.party_id), Line(acc["stock_adjustment"], credit=amount)])
    c.settled_amount, c.settled_on, c.settlement_ref, c.status = amount, on, reference, ClaimStatus.SETTLED
    audit.record(db, action="damage_claim.settled", entity_type="damage_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"amount": amount, "reference": reference})
    return c


def reject_claim(db: Session, access: BusinessAccess, c: DamageClaim, *, reason: str) -> DamageClaim:
    access.require("stock.edit")
    if c.status != ClaimStatus.OPEN:
        raise Conflict(f"This claim is {c.status.value.replace('_', ' ')}")
    c.status = ClaimStatus.REJECTED
    c.notes = f"{c.notes + ' · ' if c.notes else ''}Rejected: {reason}"[:500]
    audit.record(db, action="damage_claim.rejected", entity_type="damage_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"reason": reason})
    return c
