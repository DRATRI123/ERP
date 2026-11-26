"""Chat between people of one business: salespeople and staff talk to the owner (and each other).

Delivery is near real time without any extra server: the app keeps one "wait" request open
(``wait_for``), which returns as soon as a message for this person arrives (checked every second,
up to 20 s), then reconnects. That works on a single computer, behind Caddy, through the
online.bat tunnel and with several API workers, because the database is the only shared state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.modules.access.models import Membership, MembershipStatus
from app.modules.access.service import BusinessAccess
from app.modules.chat.models import ChatMessage
from app.modules.users.models import User

MAX_BODY = 4000


def contacts_of(db: Session, business_id: uuid.UUID) -> dict[uuid.UUID, dict[str, Any]]:
    """Everyone who can chat in this business: active members and the owner."""
    from app.modules.access.models import Role

    rows = db.execute(select(User.id, User.full_name, User.phone, Role.code, Role.name).join(Membership, Membership.user_id == User.id)
                      .join(Role, Role.id == Membership.role_id).where(
        Membership.business_id == business_id, Membership.status == MembershipStatus.ACTIVE)).all()
    out = {r[0]: {"id": r[0], "name": r[1], "phone": r[2], "role": r[3], "role_name": r[4]} for r in rows}
    for u in db.scalars(select(User).where(User.is_owner.is_(True))):
        out.setdefault(u.id, {"id": u.id, "name": u.full_name, "phone": u.phone, "role": "owner", "role_name": "Owner"})
    return out


def _check_peer(db: Session, access: BusinessAccess, peer_id: uuid.UUID) -> dict[str, Any]:
    if peer_id == access.user.id:
        raise ValidationFailed("You cannot message yourself")
    peer = contacts_of(db, access.business.id).get(peer_id)
    if peer is None:
        raise NotFound("That person is not in this business")
    return peer


def out(m: ChatMessage, me: uuid.UUID) -> dict[str, Any]:
    from app.modules.catalog import images

    return {"id": m.id, "from": m.sender_id, "to": m.recipient_id, "mine": m.sender_id == me, "body": m.body,
            "photo": images.url(m.file_id) if m.file_id else None, "thumb": images.url(m.file_id, thumb=True) if m.file_id else None,
            "document_id": m.document_id, "document_label": m.document_label, "at": m.created_at, "read_at": m.read_at}


def contacts(db: Session, access: BusinessAccess) -> list[dict[str, Any]]:
    """People to chat with, latest conversation first, with unread counts and the last message."""
    me, bid = access.user.id, access.business.id
    people = {k: v for k, v in contacts_of(db, bid).items() if k != me}
    unread = dict(db.execute(select(ChatMessage.sender_id, func.count(ChatMessage.id)).where(
        ChatMessage.business_id == bid, ChatMessage.recipient_id == me, ChatMessage.read_at.is_(None))
        .group_by(ChatMessage.sender_id)).tuples().all())
    last: dict[uuid.UUID, ChatMessage] = {}
    for m in db.scalars(select(ChatMessage).where(ChatMessage.business_id == bid,
                                                  or_(ChatMessage.sender_id == me, ChatMessage.recipient_id == me))
                        .order_by(ChatMessage.created_at.desc()).limit(500)):
        peer = m.recipient_id if m.sender_id == me else m.sender_id
        last.setdefault(peer, m)
    rows = []
    for pid, p in people.items():
        lm = last.get(pid)
        rows.append({**p, "unread": int(unread.get(pid, 0)),
                     "last": None if lm is None else {"body": lm.body or ("📷 Photo" if lm.file_id else lm.document_label),
                                                      "at": lm.created_at, "mine": lm.sender_id == me}})
    epoch = datetime.min.replace(tzinfo=UTC)
    rows.sort(key=lambda r: (r["last"]["at"] if r["last"] else epoch, r["role"] == "owner"), reverse=True)
    return rows


def thread(db: Session, access: BusinessAccess, peer_id: uuid.UUID, *, before: datetime | None, limit: int) -> list[dict[str, Any]]:
    """Messages with one person (newest page, oldest first); their messages to me become read."""
    _check_peer(db, access, peer_id)
    me, bid = access.user.id, access.business.id
    pair = or_(and_(ChatMessage.sender_id == me, ChatMessage.recipient_id == peer_id),
               and_(ChatMessage.sender_id == peer_id, ChatMessage.recipient_id == me))
    stmt = select(ChatMessage).where(ChatMessage.business_id == bid, pair)
    if before:
        stmt = stmt.where(ChatMessage.created_at < before)
    rows = list(db.scalars(stmt.order_by(ChatMessage.created_at.desc()).limit(limit)))
    mark_read(db, access, peer_id)
    return [out(m, me) for m in reversed(rows)]


def mark_read(db: Session, access: BusinessAccess, peer_id: uuid.UUID) -> int:
    res = db.execute(update(ChatMessage).where(
        ChatMessage.business_id == access.business.id, ChatMessage.sender_id == peer_id,
        ChatMessage.recipient_id == access.user.id, ChatMessage.read_at.is_(None)).values(read_at=datetime.now(UTC)))
    return int(getattr(res, "rowcount", 0) or 0)


def send(db: Session, access: BusinessAccess, *, to: uuid.UUID, body: str | None, document_id: uuid.UUID | None = None,
         file_id: uuid.UUID | None = None) -> ChatMessage:
    _check_peer(db, access, to)
    text = (body or "").strip()
    if len(text) > MAX_BODY:
        raise ValidationFailed(f"Messages up to {MAX_BODY} characters")
    label = None
    if document_id:
        from app.modules.billing.doctypes import SPECS
        from app.modules.billing.models import Document

        d = db.get(Document, document_id)
        if d is None or d.business_id != access.business.id:
            raise ValidationFailed("Document not found")
        if not access.can(SPECS[d.doc_type].view_permission):
            raise ValidationFailed("You cannot share that document")
        label = f"{SPECS[d.doc_type].label} {d.number or '(draft)'} · {d.party_name or ''} · ₹{Decimal(d.grand_total):,.2f}"[:120]
    if not text and not file_id and not document_id:
        raise ValidationFailed("Type a message")
    m = ChatMessage(business_id=access.business.id, sender_id=access.user.id, recipient_id=to, body=text or None,
                    file_id=file_id, document_id=document_id, document_label=label, created_at=datetime.now(UTC))
    db.add(m)
    db.flush()
    return m


def send_photo(db: Session, access: BusinessAccess, *, to: uuid.UUID, data: bytes, filename: str, caption: str | None) -> ChatMessage:
    from app.modules.catalog.images import MAX_SIDE, _reencode
    from app.modules.files import service as files

    _check_peer(db, access, to)
    clean, ext = _reencode(data, MAX_SIDE)  # a real image, resized, EXIF (GPS) removed
    f = files.upload(db, access, data=clean, filename=f"chat-{datetime.now(UTC):%Y%m%d-%H%M%S}.{ext}", folder="Chat photos",
                     description=f"Chat photo from {access.user.full_name}", tags=["chat-photo"])
    return send(db, access, to=to, body=caption, file_id=f.id)


def unread_total(db: Session, business_id: uuid.UUID, me: uuid.UUID) -> int:
    return int(db.scalar(select(func.count(ChatMessage.id)).where(
        ChatMessage.business_id == business_id, ChatMessage.recipient_id == me, ChatMessage.read_at.is_(None))) or 0)


def new_since(db: Session, business_id: uuid.UUID, me: uuid.UUID, after: datetime) -> list[ChatMessage]:
    """Messages to me (and mine from other devices) newer than ``after``."""
    return list(db.scalars(select(ChatMessage).where(
        ChatMessage.business_id == business_id, or_(ChatMessage.recipient_id == me, ChatMessage.sender_id == me),
        ChatMessage.created_at > after).order_by(ChatMessage.created_at).limit(200)))
