"""Enquiries to orders: leads move new → contacted → qualified → proposal → won / lost, each call
or visit is logged, and the next follow-up date drives the "call today" list."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.crm.models import ActivityKind, Lead, LeadActivity, LeadStage

CLOSED = (LeadStage.WON, LeadStage.LOST)
EDITABLE = ("name", "company", "phone", "email", "city", "source", "requirement", "expected_value", "assigned_to_id",
            "next_follow_up")


def _check_user(db: Session, access: BusinessAccess, user_id: uuid.UUID | None) -> None:
    if user_id is None:
        return
    from app.modules.access.models import Membership

    if db.scalar(select(Membership.id).where(Membership.business_id == access.business.id,
                                             Membership.user_id == user_id)) is None and user_id != access.user.id:
        raise ValidationFailed("That person is not a member of this business")


def create(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Lead:
    access.require("customers.manage")
    _check_user(db, access, data.get("assigned_to_id"))
    lead = Lead(business_id=access.business.id, created_by_id=access.user.id,
                **{k: data.get(k) for k in EDITABLE if k in data})
    lead.assigned_to_id = lead.assigned_to_id or access.user.id
    lead.expected_value = Decimal(data.get("expected_value") or 0)
    lead.stage = LeadStage.NEW
    db.add(lead)
    db.flush()
    audit.record(db, action="lead.created", entity_type="lead", entity_id=lead.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"name": lead.name, "source": lead.source})
    return lead


def get(db: Session, access: BusinessAccess, lead_id: uuid.UUID) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None or lead.business_id != access.business.id:
        raise NotFound("Lead not found")
    return lead


def update(db: Session, access: BusinessAccess, lead: Lead, data: dict[str, Any]) -> Lead:
    access.require("customers.manage")
    if "assigned_to_id" in data:
        _check_user(db, access, data["assigned_to_id"])
    for k in EDITABLE:
        if k in data:
            setattr(lead, k, data[k] if k != "expected_value" else Decimal(data[k] or 0))
    if "stage" in data and data["stage"] is not None:
        move(db, access, lead, LeadStage(data["stage"]), lost_reason=data.get("lost_reason"))
    return lead


def move(db: Session, access: BusinessAccess, lead: Lead, stage: LeadStage, *, lost_reason: str | None = None) -> Lead:
    access.require("customers.manage")
    if stage == lead.stage:
        return lead
    if stage == LeadStage.LOST and not (lost_reason or "").strip():
        raise ValidationFailed("Say why the lead was lost (price, competitor, no response…)")
    old = lead.stage
    lead.stage = stage
    if stage in CLOSED:
        lead.closed_on, lead.next_follow_up = local_today(), None
        lead.lost_reason = lost_reason.strip()[:200] if stage == LeadStage.LOST and lost_reason else None
    else:
        lead.closed_on, lead.lost_reason = None, None
    db.add(LeadActivity(business_id=access.business.id, lead_id=lead.id, kind=ActivityKind.NOTE, user_id=access.user.id,
                        happened_at=datetime.now(UTC),
                        note=f"Stage: {old.value} → {stage.value}" + (f" ({lead.lost_reason})" if lead.lost_reason else "")))
    audit.record(db, action="lead.stage", entity_type="lead", entity_id=lead.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"from": old.value, "to": stage.value})
    return lead


def log(db: Session, access: BusinessAccess, lead: Lead, *, kind: ActivityKind, note: str,
        next_follow_up: date | None) -> LeadActivity:
    """Record a call / visit / message. A new lead becomes "contacted" on its first touch."""
    access.require("customers.view")
    if lead.stage in CLOSED and next_follow_up:
        raise Conflict("This lead is closed — reopen it to plan a follow-up")
    a = LeadActivity(business_id=access.business.id, lead_id=lead.id, kind=kind, note=note.strip()[:1000],
                     happened_at=datetime.now(UTC), user_id=access.user.id)
    db.add(a)
    if lead.stage == LeadStage.NEW and kind != ActivityKind.NOTE:
        lead.stage = LeadStage.CONTACTED
    if lead.stage not in CLOSED:
        lead.next_follow_up = next_follow_up
    db.flush()
    return a


def activities(db: Session, lead: Lead) -> list[LeadActivity]:
    return list(db.scalars(select(LeadActivity).where(LeadActivity.lead_id == lead.id)
                           .order_by(LeadActivity.happened_at.desc()).limit(200)))


def listing(db: Session, access: BusinessAccess, *, stage: LeadStage | None, q: str | None, due: str | None,
            mine: bool) -> list[Lead]:
    access.require("customers.view")
    stmt = select(Lead).where(Lead.business_id == access.business.id)
    if stage:
        stmt = stmt.where(Lead.stage == stage)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Lead.name).like(like), func.lower(Lead.company).like(like), Lead.phone.like(like)))
    today = local_today()
    if due == "today":
        stmt = stmt.where(Lead.next_follow_up <= today, Lead.stage.not_in(CLOSED))
    elif due == "overdue":
        stmt = stmt.where(Lead.next_follow_up < today, Lead.stage.not_in(CLOSED))
    if mine:
        stmt = stmt.where(Lead.assigned_to_id == access.user.id)
    return list(db.scalars(stmt.order_by(Lead.next_follow_up.is_(None), Lead.next_follow_up, Lead.created_at.desc()).limit(500)))


def pipeline(db: Session, access: BusinessAccess, *, date_from: date, date_to: date) -> dict[str, Any]:
    access.require("customers.view")
    bid = access.business.id
    stages: dict[str, dict[str, Any]] = {s.value: {"count": 0, "value": Decimal(0)} for s in LeadStage}
    for st, n, v in db.execute(select(Lead.stage, func.count(Lead.id), func.coalesce(func.sum(Lead.expected_value), 0))
                               .where(Lead.business_id == bid, Lead.stage.not_in(CLOSED)).group_by(Lead.stage)).all():
        stages[st.value] = {"count": int(n), "value": Decimal(v)}
    for st, n, v in db.execute(select(Lead.stage, func.count(Lead.id), func.coalesce(func.sum(Lead.expected_value), 0))
                               .where(Lead.business_id == bid, Lead.stage.in_(CLOSED), Lead.closed_on >= date_from,
                                      Lead.closed_on <= date_to).group_by(Lead.stage)).all():
        stages[st.value] = {"count": int(n), "value": Decimal(v)}
    today = local_today()
    due = db.scalar(select(func.count(Lead.id)).where(Lead.business_id == bid, Lead.stage.not_in(CLOSED),
                                                      Lead.next_follow_up <= today)) or 0
    no_plan = db.scalar(select(func.count(Lead.id)).where(Lead.business_id == bid, Lead.stage.not_in(CLOSED),
                                                          Lead.next_follow_up.is_(None))) or 0
    sources = [{"source": s or "—", "count": int(n), "won": int(w)} for s, n, w in db.execute(
        select(Lead.source, func.count(Lead.id), func.coalesce(func.sum(case((Lead.stage == LeadStage.WON, 1), else_=0)), 0))
        .where(Lead.business_id == bid, Lead.created_at >= datetime.combine(date_from, datetime.min.time(), UTC))
        .group_by(Lead.source).order_by(func.count(Lead.id).desc()).limit(10)).all()]
    won, lost = int(stages["won"]["count"]), int(stages["lost"]["count"])
    return {"stages": stages, "follow_ups_due": int(due), "without_follow_up": int(no_plan), "sources": sources,
            "win_rate": round(won * 100 / (won + lost)) if won + lost else None}


def convert(db: Session, access: BusinessAccess, lead: Lead) -> Any:
    """Make the lead a customer (or link the existing one with the same phone) and mark it won."""
    access.require("customers.manage")
    from app.modules.parties.models import Party, PartyRole
    from app.modules.parties.service import create_party

    party = None
    if lead.party_id:
        party = db.get(Party, lead.party_id)
    if party is None and lead.phone:
        party = db.scalar(select(Party).where(Party.business_id == access.business.id, Party.phone == lead.phone).limit(1))
        if party is not None and not party.is_customer:
            party.is_customer = True
    if party is None:
        party = create_party(db, access, PartyRole.CUSTOMER, {
            "name": (lead.company or lead.name)[:200], "phone": lead.phone, "email": (lead.email or "").lower() or None,
            "gst_type": "unregistered", "attributes": {"contact_person": lead.name} if lead.company else {}},
            [{"kind": "billing", "address_line1": lead.city[:200], "city": lead.city, "country": "IN"}] if lead.city else [])
    lead.party_id = party.id
    if lead.stage != LeadStage.WON:
        move(db, access, lead, LeadStage.WON)
    return party
