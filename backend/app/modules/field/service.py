"""Field sales: beats, the salesman's day, visits and monthly targets vs achievement.

Achievement counts finalized sales invoices whose salesperson is the salesman (orders taken in
the field carry it, and invoices converted from them keep it), collections are receipts the
salesman recorded, and productive visits are those that brought an order or a payment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.modules.access.models import Membership, MembershipStatus
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType, Side
from app.modules.billing.models import DocStatus, Document, Payment, PaymentDirection, PaymentState
from app.modules.billing.service import party_outstanding
from app.modules.field.models import Beat, SalesTarget, Visit, VisitOutcome
from app.modules.parties.models import Party, PartyAddress
from app.modules.users.models import User

ZERO = Decimal(0)
PRODUCTIVE = (VisitOutcome.ORDER, VisitOutcome.PAYMENT, VisitOutcome.ORDER_AND_PAYMENT)


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


# ---------------------------------------------------------------------------- salesmen


def salesmen(db: Session, access: BusinessAccess) -> list[dict[str, Any]]:
    """Members of this business (and the owner) who can be given beats and targets."""
    rows = db.execute(select(User.id, User.full_name, User.email).join(Membership, Membership.user_id == User.id).where(
        Membership.business_id == access.business.id, Membership.status == MembershipStatus.ACTIVE)).all()
    out = {r[0]: {"id": r[0], "name": r[1], "email": r[2]} for r in rows}
    if access.is_owner and access.user.id not in out:
        out[access.user.id] = {"id": access.user.id, "name": access.user.full_name, "email": access.user.email}
    return sorted(out.values(), key=lambda x: (x["name"] or "").lower())


def _check_salesman(db: Session, access: BusinessAccess, user_id: uuid.UUID | None) -> None:
    if user_id is not None and user_id not in {s["id"] for s in salesmen(db, access)}:
        raise ValidationFailed("That person is not a member of this business")


def _check_parties(db: Session, access: BusinessAccess, ids: list[uuid.UUID]) -> list[str]:
    if len(set(ids)) != len(ids):
        raise ValidationFailed("A customer is listed twice on the beat")
    if ids:
        found = set(db.scalars(select(Party.id).where(Party.business_id == access.business.id, Party.id.in_(ids))))
        if found != set(ids):
            raise ValidationFailed("Some customers were not found")
    return [str(i) for i in ids]


# ---------------------------------------------------------------------------- beats


def list_beats(db: Session, access: BusinessAccess) -> list[Beat]:
    access.require("customers.view")
    return list(db.scalars(select(Beat).where(Beat.business_id == access.business.id).order_by(Beat.weekday, Beat.name)))


def get_beat(db: Session, access: BusinessAccess, beat_id: uuid.UUID) -> Beat:
    b = db.get(Beat, beat_id)
    if b is None or b.business_id != access.business.id:
        raise NotFound("Beat not found")
    return b


def save_beat(db: Session, access: BusinessAccess, data: dict[str, Any], beat: Beat | None = None) -> Beat:
    access.require("customers.manage")
    if "salesman_id" in data:
        _check_salesman(db, access, data["salesman_id"])
    if "party_ids" in data:
        data["party_ids"] = _check_parties(db, access, data["party_ids"])
    name = data.get("name", beat.name if beat else None)
    dup = db.scalar(select(Beat.id).where(Beat.business_id == access.business.id, func.lower(Beat.name) == (name or "").lower(),
                                          *([Beat.id != beat.id] if beat else [])))
    if dup:
        raise ValidationFailed(f"A beat named {name} already exists")
    if beat is None:
        beat = Beat(business_id=access.business.id, **data)
        db.add(beat)
    else:
        for k, v in data.items():
            setattr(beat, k, v)
    db.flush()
    audit.record(db, action="beat.saved", entity_type="beat", entity_id=beat.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": beat.name, "customers": len(beat.party_ids)})
    return beat


# ---------------------------------------------------------------------------- the day


def _party_cards(db: Session, access: BusinessAccess, party_ids: list[uuid.UUID], day: date) -> list[dict[str, Any]]:
    if not party_ids:
        return []
    parties = {p.id: p for p in db.scalars(select(Party).where(Party.id.in_(party_ids)))}
    addresses: dict[uuid.UUID, PartyAddress] = {}
    for addr in db.scalars(select(PartyAddress).where(PartyAddress.party_id.in_(party_ids)).order_by(PartyAddress.is_default.desc())):
        addresses.setdefault(addr.party_id, addr)
    last = dict(db.execute(select(Visit.party_id, func.max(Visit.visit_date)).where(
        Visit.business_id == access.business.id, Visit.party_id.in_(party_ids)).group_by(Visit.party_id)).all())
    today = {v.party_id: v for v in db.scalars(select(Visit).where(
        Visit.business_id == access.business.id, Visit.party_id.in_(party_ids), Visit.visit_date == day))}
    show_dues = access.can("customer_balances.view")
    out = []
    for pid in party_ids:
        p = parties.get(pid)
        if p is None:
            continue
        a = addresses.get(pid)
        v = today.get(pid)
        out.append({"party_id": pid, "name": p.name, "phone": p.phone,
                    "address": ", ".join(x for x in (a.address_line1, a.city) if x) if a else None,
                    "outstanding": party_outstanding(db, pid, Side.SALES) if show_dues else None,
                    "credit_hold": p.credit_hold, "last_visit": last.get(pid),
                    "visited_today": v.outcome.value if v else None})
    return out


def my_day(db: Session, access: BusinessAccess, *, day: date, salesman_id: uuid.UUID | None = None,
           beat_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Today's beat for the salesman (or the one asked for), with each customer's dues and visit."""
    access.require("customers.view")
    who = salesman_id or access.user.id
    if who != access.user.id and not access.can("reports.view"):
        raise ValidationFailed("You can only see your own day")
    mine = [b for b in list_beats(db, access) if b.is_active and b.salesman_id == who]
    beat: Beat | None
    if beat_id:
        beat = get_beat(db, access, beat_id)
    else:
        beat = next((b for b in mine if b.weekday == day.weekday()), None) or next((b for b in mine if b.weekday is None), None)
    visits = list(db.scalars(select(Visit).where(Visit.business_id == access.business.id, Visit.salesman_id == who,
                                                 Visit.visit_date == day)))
    ids = [uuid.UUID(x) for x in beat.party_ids] if beat else []
    return {"day": day, "salesman_id": who, "beat": {"id": beat.id, "name": beat.name} if beat else None,
            "beats": [{"id": b.id, "name": b.name, "weekday": b.weekday} for b in mine],
            "customers": _party_cards(db, access, ids, day),
            "visits_today": len(visits), "productive_today": sum(1 for v in visits if v.outcome in PRODUCTIVE)}


def record_visit(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Visit:
    access.require("customers.view")
    party = db.get(Party, data["party_id"])
    if party is None or party.business_id != access.business.id:
        raise ValidationFailed("Customer not found")
    doc = db.get(Document, data["document_id"]) if data.get("document_id") else None
    pay = db.get(Payment, data["payment_id"]) if data.get("payment_id") else None
    if (data.get("document_id") and (doc is None or doc.business_id != access.business.id)) or \
            (data.get("payment_id") and (pay is None or pay.business_id != access.business.id)):
        raise ValidationFailed("Linked order or payment not found")
    if data.get("beat_id"):
        get_beat(db, access, data["beat_id"])
    v = Visit(business_id=access.business.id, salesman_id=access.user.id, at=datetime.now(UTC), **data)
    db.add(v)
    db.flush()
    return v


def list_visits(db: Session, access: BusinessAccess, *, date_from: date, date_to: date,
                salesman_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    access.require("customers.view")
    stmt = (select(Visit, Party.name, User.full_name).join(Party, Party.id == Visit.party_id).join(User, User.id == Visit.salesman_id)
            .where(Visit.business_id == access.business.id, Visit.visit_date >= date_from, Visit.visit_date <= date_to))
    if not access.can("reports.view"):
        stmt = stmt.where(Visit.salesman_id == access.user.id)
    elif salesman_id:
        stmt = stmt.where(Visit.salesman_id == salesman_id)
    return [{"id": v.id, "visit_date": v.visit_date, "at": v.at, "party_id": v.party_id, "party_name": pn,
             "salesman_id": v.salesman_id, "salesman_name": sn, "outcome": v.outcome.value, "note": v.note,
             "document_id": v.document_id, "payment_id": v.payment_id, "latitude": v.latitude, "longitude": v.longitude,
             "next_visit": v.next_visit}
            for v, pn, sn in db.execute(stmt.order_by(Visit.at.desc()).limit(1000)).all()]


# ---------------------------------------------------------------------------- targets


def set_target(db: Session, access: BusinessAccess, *, salesman_id: uuid.UUID, month: date, amount: Decimal,
               collection_amount: Decimal | None) -> SalesTarget:
    access.require("users.manage")
    _check_salesman(db, access, salesman_id)
    m = month_start(month)
    t = db.scalar(select(SalesTarget).where(SalesTarget.business_id == access.business.id,
                                            SalesTarget.salesman_id == salesman_id, SalesTarget.month == m))
    if t is None:
        t = SalesTarget(business_id=access.business.id, salesman_id=salesman_id, month=m, amount=amount,
                        collection_amount=collection_amount)
        db.add(t)
    else:
        t.amount, t.collection_amount = amount, collection_amount
    db.flush()
    audit.record(db, action="sales_target.set", entity_type="sales_target", entity_id=t.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"salesman_id": salesman_id, "month": m, "amount": amount})
    return t


def targets(db: Session, access: BusinessAccess, *, month: date, today: date) -> dict[str, Any]:
    """Each salesman's target, sales, orders, collections and visits for the month, with a pace check."""
    access.require("customers.view")
    start, end = month_start(month), month_end(month)
    bid = access.business.id
    people = salesmen(db, access)
    if not access.can("reports.view"):
        people = [p for p in people if p["id"] == access.user.id]
    ids = [p["id"] for p in people]

    def by_user(stmt: Any) -> dict[uuid.UUID, tuple[Decimal, int]]:
        return {u: (Decimal(v or 0), int(n)) for u, v, n in db.execute(stmt).all()}

    tgt = {t.salesman_id: t for t in db.scalars(select(SalesTarget).where(
        SalesTarget.business_id == bid, SalesTarget.month == start, SalesTarget.salesman_id.in_(ids)))}
    sales = by_user(select(Document.salesperson_id, func.sum(Document.taxable_total), func.count(Document.id)).where(
        Document.business_id == bid, Document.doc_type == DocType.SALES_INVOICE, Document.status == DocStatus.FINAL,
        Document.doc_date >= start, Document.doc_date <= end, Document.salesperson_id.in_(ids)).group_by(Document.salesperson_id))
    returns = by_user(select(Document.salesperson_id, func.sum(Document.taxable_total), func.count(Document.id)).where(
        Document.business_id == bid, Document.doc_type == DocType.CREDIT_NOTE, Document.status == DocStatus.FINAL,
        Document.doc_date >= start, Document.doc_date <= end, Document.salesperson_id.in_(ids)).group_by(Document.salesperson_id))
    orders = by_user(select(Document.salesperson_id, func.sum(Document.taxable_total), func.count(Document.id)).where(
        Document.business_id == bid, Document.doc_type == DocType.SALES_ORDER, Document.status != DocStatus.CANCELLED,
        Document.doc_date >= start, Document.doc_date <= end, Document.salesperson_id.in_(ids)).group_by(Document.salesperson_id))
    collections = by_user(select(Payment.created_by_id, func.sum(Payment.amount), func.count(Payment.id)).where(
        Payment.business_id == bid, Payment.direction == PaymentDirection.IN, Payment.status == PaymentState.POSTED,
        Payment.payment_date >= start, Payment.payment_date <= end, Payment.created_by_id.in_(ids)).group_by(Payment.created_by_id))
    visits: dict[uuid.UUID, list[int]] = {}
    for u, outcome, n in db.execute(select(Visit.salesman_id, Visit.outcome, func.count(Visit.id)).where(
            Visit.business_id == bid, Visit.visit_date >= start, Visit.visit_date <= end, Visit.salesman_id.in_(ids))
            .group_by(Visit.salesman_id, Visit.outcome)).all():
        v = visits.setdefault(u, [0, 0])
        v[0] += int(n)
        v[1] += int(n) if outcome in PRODUCTIVE else 0
    days, elapsed = (end - start).days + 1, max(0, min((today - start).days + 1, (end - start).days + 1))
    rows = []
    for p in people:
        u = p["id"]
        t = tgt.get(u)
        net = sales.get(u, (ZERO, 0))[0] - returns.get(u, (ZERO, 0))[0]
        target = t.amount if t else None
        expected = target * elapsed / days if target else None
        rows.append({
            "salesman_id": u, "name": p["name"], "target": target, "collection_target": t.collection_amount if t else None,
            "sales": net, "invoices": sales.get(u, (ZERO, 0))[1], "orders": orders.get(u, (ZERO, 0))[0],
            "order_count": orders.get(u, (ZERO, 0))[1], "collections": collections.get(u, (ZERO, 0))[0],
            "visits": visits.get(u, [0, 0])[0], "productive_visits": visits.get(u, [0, 0])[1],
            "achieved_percent": round(float(net / target * 100), 1) if target else None,
            "on_pace": (net >= expected) if expected is not None else None,
        })
    rows.sort(key=lambda r: r["sales"], reverse=True)
    return {"month": start, "days": days, "days_elapsed": elapsed, "rows": rows}


def my_summary(db: Session, access: BusinessAccess, *, today: date) -> dict[str, Any]:
    """The salesperson portal's home screen: today's work, the month against target, and the
    latest orders and collections this person booked."""
    access.require("customers.view")
    me, bid = access.user.id, access.business.id
    orders_q = select(Document).where(Document.business_id == bid, Document.doc_type == DocType.SALES_ORDER,
                                      Document.salesperson_id == me, Document.status != DocStatus.CANCELLED)
    today_orders = list(db.scalars(orders_q.where(Document.doc_date == today)))
    coll_q = select(Payment).where(Payment.business_id == bid, Payment.direction == PaymentDirection.IN,
                                   Payment.status == PaymentState.POSTED, Payment.created_by_id == me)
    today_coll = list(db.scalars(coll_q.where(Payment.payment_date == today)))
    v_today = list(db.scalars(select(Visit).where(Visit.business_id == bid, Visit.salesman_id == me, Visit.visit_date == today)))
    month = targets(db, access, month=today, today=today)
    mine = next((r for r in month["rows"] if r["salesman_id"] == me), None)
    recent = list(db.scalars(orders_q.order_by(Document.doc_date.desc(), Document.created_at.desc()).limit(20)))
    invoiced = set(db.scalars(select(Document.reference_document_id).where(
        Document.reference_document_id.in_([d.id for d in recent]), Document.status == DocStatus.FINAL))) if recent else set()
    payments = list(db.scalars(coll_q.order_by(Payment.payment_date.desc(), Payment.created_at.desc()).limit(10)))
    names = dict(db.execute(select(Party.id, Party.name).where(
        Party.id.in_({p.party_id for p in payments if p.party_id}))).all()) if payments else {}
    return {
        "name": access.user.full_name, "business": access.business.name, "day": today,
        "today": {"orders": len(today_orders), "order_value": sum((d.grand_total for d in today_orders), Decimal(0)),
                  "collected": sum((p.amount for p in today_coll), Decimal(0)), "collections": len(today_coll),
                  "visits": len(v_today), "productive": sum(1 for v in v_today if v.outcome in (VisitOutcome.ORDER, VisitOutcome.PAYMENT, VisitOutcome.ORDER_AND_PAYMENT))},
        "month": mine,
        "orders": [{"id": d.id, "number": d.number, "doc_date": d.doc_date, "party_name": d.party_name,
                    "total": d.grand_total, "status": d.status, "invoiced": d.id in invoiced} for d in recent],
        "collections": [{"id": p.id, "date": p.payment_date, "party_name": names.get(p.party_id) if p.party_id else None, "amount": p.amount,
                         "mode": p.mode, "reference": p.reference} for p in payments],
    }
