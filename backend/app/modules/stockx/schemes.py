"""Scheme / rate-difference claims: the free goods and scheme discounts given to customers on a
manufacturer's behalf, claimed back from that manufacturer.

A scheme names who pays for it (``claim_party_id``) and what share (``claim_percent``). Free goods
are valued at our purchase price (what the manufacturer replaces), discounts at the discount given.
Settling a claim reduces what we owe the manufacturer: Dr Accounts payable, Cr Scheme claims received.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.chart import Accounts
from app.modules.accounting.ledger import Line, post
from app.modules.accounting.models import JournalSource
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document, DocumentLine
from app.modules.catalog.models import Product
from app.modules.numbering.service import next_number
from app.modules.parties.models import Party
from app.modules.pricing.models import Scheme
from app.modules.pricing.service import FREE_FLAG
from app.modules.stockx.models import SchemeClaim, SchemeClaimStatus

ZERO = Decimal(0)


def q2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _claimed_ids(db: Session, business_id: uuid.UUID) -> set[str]:
    out: set[str] = set()
    for ids in db.scalars(select(SchemeClaim.line_ids).where(SchemeClaim.business_id == business_id,
                                                             SchemeClaim.status != SchemeClaimStatus.REJECTED)):
        out.update(ids or [])
    return out


def claimable(db: Session, access: BusinessAccess, *, party_id: uuid.UUID | None, date_from: date,
              date_to: date) -> dict[str, Any]:
    """Unclaimed scheme lines on final sales invoices, grouped by the manufacturer who pays."""
    access.require("reports.view")
    bid = access.business.id
    stmt = select(Scheme).where(Scheme.business_id == bid, Scheme.claim_party_id.is_not(None))
    if party_id:
        stmt = stmt.where(Scheme.claim_party_id == party_id)
    schemes = {str(s.id): s for s in db.scalars(stmt)}
    if not schemes:
        return {"parties": [], "total": ZERO}
    claimed = _claimed_ids(db, bid)
    rows = db.execute(select(DocumentLine, Document).join(Document, Document.id == DocumentLine.document_id).where(
        Document.business_id == bid, Document.doc_type == DocType.SALES_INVOICE, Document.status == DocStatus.FINAL,
        Document.doc_date >= date_from, Document.doc_date <= date_to).order_by(Document.doc_date, Document.number)).all()
    prices: dict[uuid.UUID, Decimal | None] = {}
    groups: dict[uuid.UUID, dict[str, Any]] = {}
    for ln, doc in rows:
        sid = (ln.custom_fields or {}).get("scheme_id")
        s = schemes.get(sid or "")
        if s is None or s.claim_party_id is None or str(ln.id) in claimed:
            continue
        free = bool((ln.custom_fields or {}).get(FREE_FLAG))
        if free and ln.product_id:
            if ln.product_id not in prices:
                p = db.get(Product, ln.product_id)
                prices[ln.product_id] = p.purchase_price if p else None
            cost = prices[ln.product_id]
            base = ln.quantity * cost if cost else ln.discount_total
        else:
            base = ln.discount_total
        value = q2(base * s.claim_percent / 100)
        if value <= 0:
            continue
        g = groups.setdefault(s.claim_party_id, {"party_id": s.claim_party_id, "lines": [], "free_quantity": ZERO, "amount": ZERO})
        g["lines"].append({"line_id": ln.id, "document_id": doc.id, "number": doc.number, "doc_date": doc.doc_date,
                           "customer": doc.party_name, "scheme": s.name, "description": ln.description,
                           "free": free, "quantity": ln.quantity, "value": value})
        if free:
            g["free_quantity"] += ln.quantity
        g["amount"] += value
    out = []
    for g in groups.values():
        party = db.get(Party, g["party_id"])
        out.append({**g, "party_name": party.name if party else "—"})
    return {"parties": sorted(out, key=lambda g: g["party_name"]), "total": sum((g["amount"] for g in out), ZERO)}


def create_claim(db: Session, access: BusinessAccess, *, party_id: uuid.UUID, date_from: date, date_to: date,
                 on: date, notes: str | None) -> SchemeClaim:
    access.require("purchases.edit")
    data = claimable(db, access, party_id=party_id, date_from=date_from, date_to=date_to)
    g = next((g for g in data["parties"] if g["party_id"] == party_id), None)
    if g is None or not g["lines"]:
        raise ValidationFailed("Nothing to claim from this party in these dates")
    from app.modules.inventory.service import _open_fy

    fy = _open_fy(db, access.business.id, on).code
    c = SchemeClaim(business_id=access.business.id, number=next_number(db, access.business.id, f"scheme_claim/{fy}",
                                                                        prefix=f"SCH/{fy}/"),
                    claim_date=on, party_id=party_id, date_from=date_from, date_to=date_to,
                    line_ids=[str(ln["line_id"]) for ln in g["lines"]], free_quantity=g["free_quantity"],
                    claim_amount=g["amount"], status=SchemeClaimStatus.OPEN, notes=notes, created_by_id=access.user.id)
    db.add(c)
    db.flush()
    audit.record(db, action="scheme_claim.created", entity_type="scheme_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": c.number, "amount": c.claim_amount, "lines": len(c.line_ids)})
    return c


def get_claim(db: Session, access: BusinessAccess, claim_id: uuid.UUID) -> SchemeClaim:
    c = db.get(SchemeClaim, claim_id)
    if c is None or c.business_id != access.business.id:
        raise NotFound("Claim not found")
    return c


def list_claims(db: Session, access: BusinessAccess) -> list[SchemeClaim]:
    access.require("reports.view")
    return list(db.scalars(select(SchemeClaim).where(SchemeClaim.business_id == access.business.id)
                           .order_by(SchemeClaim.claim_date.desc(), SchemeClaim.number.desc()).limit(500)))


def claim_lines(db: Session, access: BusinessAccess, c: SchemeClaim) -> list[dict[str, Any]]:
    ids = [uuid.UUID(i) for i in c.line_ids]
    rows = db.execute(select(DocumentLine, Document).join(Document, Document.id == DocumentLine.document_id)
                      .where(DocumentLine.id.in_(ids), Document.business_id == access.business.id)
                      .order_by(Document.doc_date)).all()
    return [{"number": d.number, "doc_date": d.doc_date, "customer": d.party_name, "description": ln.description,
             "quantity": ln.quantity, "free": bool((ln.custom_fields or {}).get(FREE_FLAG)),
             "scheme": (ln.custom_fields or {}).get("scheme")} for ln, d in rows]


def settle(db: Session, access: BusinessAccess, c: SchemeClaim, *, amount: Decimal, on: date,
           reference: str | None) -> SchemeClaim:
    access.require("purchases.edit")
    if c.status != SchemeClaimStatus.OPEN:
        raise Conflict(f"This claim is {c.status.value}")
    amount = q2(amount)
    if amount <= 0:
        raise ValidationFailed("Enter the amount the manufacturer credited")
    acc = Accounts(db, access.business.id)
    post(db, access.business.id, entry_date=on, source_type=JournalSource.CLAIM, source_id=c.id,
         source_ref=reference or c.number, created_by=access.user.id,
         narration=f"Scheme claim {c.number} settled" + (f" (credit note {reference})" if reference else ""),
         lines=[Line(acc["ap"], debit=amount, party_id=c.party_id), Line(acc["scheme_claims"], credit=amount)])
    c.settled_amount, c.settled_on, c.settlement_ref, c.status = amount, on, reference, SchemeClaimStatus.SETTLED
    audit.record(db, action="scheme_claim.settled", entity_type="scheme_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"amount": amount, "reference": reference})
    return c


def reject(db: Session, access: BusinessAccess, c: SchemeClaim, *, reason: str) -> SchemeClaim:
    """The lines become claimable again (e.g. to re-claim with corrected figures)."""
    access.require("purchases.edit")
    if c.status != SchemeClaimStatus.OPEN:
        raise Conflict(f"This claim is {c.status.value}")
    c.status = SchemeClaimStatus.REJECTED
    c.notes = f"{c.notes + ' · ' if c.notes else ''}Rejected: {reason}"[:500]
    audit.record(db, action="scheme_claim.rejected", entity_type="scheme_claim", entity_id=c.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"reason": reason})
    return c
