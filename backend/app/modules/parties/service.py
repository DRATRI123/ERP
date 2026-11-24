"""Customers and suppliers.

A party row can be a customer, a supplier, or both. Every operation is performed "as" one role,
and the caller needs that role's permission (``customers.*`` or ``suppliers.*``). Turning on the
other role additionally requires the other role's manage permission.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.events import service as events
from app.modules.parties.models import (
    AddressKind,
    Party,
    PartyAddress,
    PartyGroup,
    PartyGstType,
    PartyRole,
)

PARTY_FIELDS = [
    "code", "name", "is_customer", "is_supplier", "gst_type", "gstin", "pan", "state_code",
    "contact_person", "phone", "email", "customer_group_id", "supplier_group_id",
    "credit_limit", "credit_days", "notes", "attributes", "is_active",
]
ADDRESS_FIELDS = ["kind", "label", "address_line1", "address_line2", "city", "state_code",
                  "pincode", "country", "is_default"]


def _flag(role: PartyRole) -> str:
    return "is_customer" if role == PartyRole.CUSTOMER else "is_supplier"


def _perm(role: PartyRole, action: str) -> str:
    return f"{'customers' if role == PartyRole.CUSTOMER else 'suppliers'}.{action}"


def _record(db: Session, access: BusinessAccess, action: str, eid: Any, changes: dict | None = None) -> None:
    audit.record(db, action=action, entity_type="party", entity_id=eid,
                 business_id=access.business.id, actor_id=access.user.id, changes=changes)


def _emit(db: Session, access: BusinessAccess, p: Party, kind: str, payload: dict) -> None:
    # Webhook vocabulary is customer.* (supplier events arrive with purchases in Phase 3).
    if p.is_customer:
        events.emit(db, f"customer.{kind}", aggregate_type="party", aggregate_id=p.id,
                    business_id=access.business.id, payload=payload)


# ---------------------------------------------------------------------------- groups


def list_groups(db: Session, access: BusinessAccess, role: PartyRole) -> list[PartyGroup]:
    return list(db.scalars(select(PartyGroup).where(
        PartyGroup.business_id == access.business.id, PartyGroup.role == role).order_by(PartyGroup.name)))


def get_group(db: Session, access: BusinessAccess, gid: uuid.UUID, role: PartyRole) -> PartyGroup:
    g = db.get(PartyGroup, gid)
    if g is None or g.business_id != access.business.id or g.role != role:
        raise NotFound(f"{role.value.title()} group not found")
    return g


def create_group(db: Session, access: BusinessAccess, role: PartyRole, name: str) -> PartyGroup:
    access.require(_perm(role, "manage"))
    if db.scalar(select(PartyGroup.id).where(
            PartyGroup.business_id == access.business.id, PartyGroup.role == role,
            func.lower(PartyGroup.name) == name.lower())):
        raise Conflict(f"Group {name} already exists")
    g = PartyGroup(business_id=access.business.id, role=role, name=name)
    db.add(g)
    db.flush()
    audit.record(db, action="party_group.created", entity_type="party_group", entity_id=g.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"role": role.value, "name": name})
    return g


def rename_group(db: Session, access: BusinessAccess, g: PartyGroup, name: str) -> PartyGroup:
    access.require(_perm(g.role, "manage"))
    if db.scalar(select(PartyGroup.id).where(
            PartyGroup.business_id == access.business.id, PartyGroup.role == g.role,
            PartyGroup.id != g.id, func.lower(PartyGroup.name) == name.lower())):
        raise Conflict(f"Group {name} already exists")
    old, g.name = g.name, name
    audit.record(db, action="party_group.renamed", entity_type="party_group", entity_id=g.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"name": {"from": old, "to": name}})
    return g


# ---------------------------------------------------------------------------- parties


def get_party(db: Session, access: BusinessAccess, pid: uuid.UUID, role: PartyRole) -> Party:
    p = db.get(Party, pid)
    if p is None or p.business_id != access.business.id or not getattr(p, _flag(role)):
        raise NotFound(f"{role.value.title()} not found")
    return p


def search(access: BusinessAccess, role: PartyRole, *, q: str | None, group_id: uuid.UUID | None,
           include_archived: bool) -> Select:
    stmt = select(Party).where(Party.business_id == access.business.id,
                               getattr(Party, _flag(role)).is_(True))
    if not include_archived:
        stmt = stmt.where(Party.is_active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Party.name).like(like), Party.phone.like(f"%{q}%"),
                              Party.gstin == q.upper(), func.lower(Party.code) == q.lower(),
                              func.lower(Party.email) == q.lower()))
    if group_id:
        col = Party.customer_group_id if role == PartyRole.CUSTOMER else Party.supplier_group_id
        stmt = stmt.where(col == group_id)
    return stmt.order_by(Party.name)


def _normalise(db: Session, access: BusinessAccess, data: dict[str, Any], current: Party | None) -> None:
    merged = {f: getattr(current, f) for f in PARTY_FIELDS} if current else {}
    merged.update(data)
    gstin = merged.get("gstin")
    if gstin:
        # The GSTIN fixes the state (place of supply) and the PAN.
        if merged.get("gst_type") in (PartyGstType.UNREGISTERED, PartyGstType.CONSUMER,
                                      PartyGstType.OVERSEAS, None):
            data["gst_type"] = merged["gst_type"] = PartyGstType.REGULAR
        if data.get("state_code") and data["state_code"] != gstin[:2]:
            raise ValidationFailed("State code does not match the GSTIN")
        data["state_code"] = gstin[:2]
        if merged.get("pan") and merged["pan"] != gstin[2:12]:
            raise ValidationFailed("PAN does not match the GSTIN")
        data["pan"] = gstin[2:12]
        dup = select(Party.id).where(Party.business_id == access.business.id, Party.gstin == gstin)
        if current:
            dup = dup.where(Party.id != current.id)
        if db.scalar(dup):
            raise Conflict("Another party already has this GSTIN")
    elif merged.get("gst_type") in (PartyGstType.REGULAR, PartyGstType.COMPOSITION, PartyGstType.SEZ):
        raise ValidationFailed("A GSTIN is required for registered parties")
    if data.get("code"):
        dup = select(Party.id).where(Party.business_id == access.business.id, Party.code == data["code"])
        if current:
            dup = dup.where(Party.id != current.id)
        if db.scalar(dup):
            raise Conflict(f"Party code {data['code']} is already in use")
    if data.get("customer_group_id"):
        get_group(db, access, data["customer_group_id"], PartyRole.CUSTOMER)
    if data.get("supplier_group_id"):
        get_group(db, access, data["supplier_group_id"], PartyRole.SUPPLIER)


def _sync_default(p: Party, addr: PartyAddress) -> None:
    if addr.is_default:
        for other in p.addresses:
            if other is not addr and other.kind == addr.kind:
                other.is_default = False


def create_party(db: Session, access: BusinessAccess, role: PartyRole, data: dict[str, Any],
                 addresses: list[dict[str, Any]]) -> Party:
    access.require(_perm(role, "manage"))
    data[_flag(role)] = True
    other = PartyRole.SUPPLIER if role == PartyRole.CUSTOMER else PartyRole.CUSTOMER
    if data.get(_flag(other)):
        access.require(_perm(other, "manage"))
    _normalise(db, access, data, None)
    p = Party(business_id=access.business.id, **data)
    for a in addresses:
        addr = PartyAddress(**a)
        p.addresses.append(addr)
        _sync_default(p, addr)
    for kind in AddressKind:  # first address of each kind becomes the default
        of_kind = [a for a in p.addresses if a.kind == kind]
        if of_kind and not any(a.is_default for a in of_kind):
            of_kind[0].is_default = True
    db.add(p)
    db.flush()
    snap = audit.snapshot(p, PARTY_FIELDS)
    _record(db, access, "party.created", p.id, snap)
    _emit(db, access, p, "created", {"id": p.id, **snap})
    return p


def update_party(db: Session, access: BusinessAccess, role: PartyRole, p: Party,
                 data: dict[str, Any]) -> Party:
    access.require(_perm(role, "manage"))
    if data.get(_flag(role)) is False and not getattr(
            p, _flag(PartyRole.SUPPLIER if role == PartyRole.CUSTOMER else PartyRole.CUSTOMER)):
        raise ValidationFailed("A party must remain a customer or a supplier; archive it instead")
    for r in PartyRole:
        if r != role and _flag(r) in data and data[_flag(r)] != getattr(p, _flag(r)):
            access.require(_perm(r, "manage"))
    _normalise(db, access, data, p)
    before = audit.snapshot(p, PARTY_FIELDS)
    for k, v in data.items():
        setattr(p, k, v)
    changes = audit.diff(before, audit.snapshot(p, PARTY_FIELDS))
    if changes:
        _record(db, access, "party.updated", p.id, changes)
        _emit(db, access, p, "updated", {"id": p.id, "changes": changes})
    return p


def set_archived(db: Session, access: BusinessAccess, role: PartyRole, p: Party, archived: bool) -> Party:
    access.require(_perm(role, "manage"))
    if p.is_active == (not archived):
        return p
    p.is_active = not archived
    p.archived_at = datetime.now(UTC) if archived else None
    _record(db, access, "party.archived" if archived else "party.restored", p.id)
    _emit(db, access, p, "deleted" if archived else "updated",
          {"id": p.id, "is_active": p.is_active})
    return p


def add_address(db: Session, access: BusinessAccess, role: PartyRole, p: Party,
                data: dict[str, Any]) -> PartyAddress:
    access.require(_perm(role, "manage"))
    addr = PartyAddress(**data)
    if not any(a.kind == addr.kind for a in p.addresses):
        addr.is_default = True
    p.addresses.append(addr)
    _sync_default(p, addr)
    db.flush()
    _record(db, access, "party.address_added", p.id, audit.snapshot(addr, ADDRESS_FIELDS))
    return addr


def _address(p: Party, address_id: uuid.UUID) -> PartyAddress:
    addr = next((a for a in p.addresses if a.id == address_id), None)
    if addr is None:
        raise NotFound("Address not found")
    return addr


def update_address(db: Session, access: BusinessAccess, role: PartyRole, p: Party,
                   address_id: uuid.UUID, data: dict[str, Any]) -> PartyAddress:
    access.require(_perm(role, "manage"))
    addr = _address(p, address_id)
    before = audit.snapshot(addr, ADDRESS_FIELDS)
    for k, v in data.items():
        setattr(addr, k, v)
    _sync_default(p, addr)
    _record(db, access, "party.address_updated", p.id,
            audit.diff(before, audit.snapshot(addr, ADDRESS_FIELDS)))
    return addr


def delete_address(db: Session, access: BusinessAccess, role: PartyRole, p: Party,
                   address_id: uuid.UUID) -> None:
    access.require(_perm(role, "manage"))
    addr = _address(p, address_id)
    p.addresses.remove(addr)
    if addr.is_default:
        rest = [a for a in p.addresses if a.kind == addr.kind]
        if rest:
            rest[0].is_default = True
    _record(db, access, "party.address_removed", p.id, audit.snapshot(addr, ADDRESS_FIELDS))
