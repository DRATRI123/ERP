"""Chat between salespeople, staff and the owner of a business."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import DB, require
from app.core.errors import ValidationFailed
from app.db.session import SessionLocal
from app.modules.access.service import BusinessAccess
from app.modules.chat import service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/businesses/{business_id}/chat", tags=["chat"])
Member = Annotated[BusinessAccess, Depends(require("chat.use"))]
WAIT_SECONDS = 20  # below the 30 s proxy limit of Next.js / tunnels


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: uuid.UUID
    body: Annotated[str, Field(max_length=service.MAX_BODY)] | None = None
    document_id: uuid.UUID | None = None


@router.get("/contacts")
def contacts(access: Member, db: DB) -> dict[str, Any]:
    return {"contacts": service.contacts(db, access), "unread": service.unread_total(db, access.business.id, access.user.id),
            "me": access.user.id, "server_time": datetime.now(UTC)}


@router.get("/with/{peer_id}")
def thread(peer_id: uuid.UUID, access: Member, db: DB, before: datetime | None = None,
           limit: Annotated[int, Query(ge=1, le=200)] = 60) -> list[dict[str, Any]]:
    rows = service.thread(db, access, peer_id, before=before, limit=limit)
    db.commit()  # read receipts
    from app.modules.chat.hub import hub

    hub.read_threadsafe(access.business.id, access.user.id, peer_id)
    return rows


@router.post("/with/{peer_id}/read")
def read(peer_id: uuid.UUID, access: Member, db: DB) -> dict[str, int]:
    n = service.mark_read(db, access, peer_id)
    db.commit()
    from app.modules.chat.hub import hub

    hub.read_threadsafe(access.business.id, access.user.id, peer_id)
    return {"marked": n}


@router.post("/messages", status_code=status.HTTP_201_CREATED)
def send(body: SendIn, access: Member, db: DB) -> dict[str, Any]:
    m = service.send(db, access, to=body.to, body=body.body, document_id=body.document_id)
    db.commit()
    _push(access, m, db)
    return service.out(m, access.user.id)


def _push(access: BusinessAccess, m: Any, db: Any) -> None:
    from app.modules.chat.hub import hub

    hub.deliver_threadsafe(access.business.id, m, service.unread_total(db, access.business.id, m.recipient_id))


@router.post("/photos", status_code=status.HTTP_201_CREATED)
async def send_photo(access: Member, db: DB, to: Annotated[uuid.UUID, Form()], file: UploadFile = File(...),
                     caption: Annotated[str | None, Form(max_length=1000)] = None) -> dict[str, Any]:
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise ValidationFailed("Photos up to 15 MB")
    m = await run_in_threadpool(service.send_photo, db, access, to=to, data=data, filename=file.filename or "photo.jpg", caption=caption)
    await run_in_threadpool(db.commit)
    from app.modules.chat.hub import hub

    unread = await run_in_threadpool(service.unread_total, db, access.business.id, m.recipient_id)
    await hub.deliver(access.business.id, m, {m.recipient_id: unread})
    return service.out(m, access.user.id)


@router.get("/wait")
async def wait(access: Member, db: DB, after: datetime) -> dict[str, Any]:
    """Returns as soon as there is a message newer than ``after`` for this person (or after 20 s
    with none). The app calls it again straight away, so messages appear within about a second."""
    bid, me = access.business.id, access.user.id
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    after = max(after, datetime.now(UTC) - timedelta(days=2))
    await run_in_threadpool(db.close)  # do not hold a database connection while waiting

    def check() -> tuple[list[dict[str, Any]], int]:
        with SessionLocal() as s:
            rows = service.new_since(s, bid, me, after)
            return [service.out(m, me) for m in rows], service.unread_total(s, bid, me)

    for _ in range(WAIT_SECONDS):
        messages, unread = await run_in_threadpool(check)
        if messages:
            return {"messages": messages, "unread": unread, "server_time": datetime.now(UTC)}
        await asyncio.sleep(1)
    return {"messages": [], "unread": unread, "server_time": datetime.now(UTC)}


# ---------------------------------------------------------------------------- WebSocket

ws_router = APIRouter(tags=["chat"])


def _ws_identity(token: str, business_id: str) -> tuple[uuid.UUID, uuid.UUID, str] | str:
    """Same checks as every API request: valid token, live session, active user, business member
    with the chat permission. Returns (business, user, name) or an error text."""
    import jwt as pyjwt

    from app.core.security import decode_access_token
    from app.modules.access.service import get_business_access
    from app.modules.auth.models import UserSession
    from app.modules.users.models import User, UserStatus

    try:
        claims = decode_access_token(token)
        user_id, session_id, bid = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"]), uuid.UUID(business_id)
    except (pyjwt.PyJWTError, ValueError, KeyError, TypeError):
        return "Invalid or expired access token"
    with SessionLocal() as db:
        session = db.get(UserSession, session_id)
        if session is None or session.user_id != user_id or session.revoked_at is not None or session.expires_at <= datetime.now(UTC):
            return "Session has ended"
        user = db.get(User, user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            return "Account not active"
        try:
            access = get_business_access(db, user, bid)
        except Exception:  # noqa: BLE001 - not a member, unknown business
            return "No access to this business"
        if not (access.is_owner or access.can("chat.use")):
            return "No chat permission"
        return bid, user_id, user.full_name


@ws_router.websocket("/chat/ws")
async def chat_socket(ws: WebSocket) -> None:
    """Live chat. First frame: {"type": "auth", "token": <access token>, "business_id": …}.
    Then the server pushes {"type": "message" | "typing" | "presence" | "read", …}; the client may
    send {"type": "send", "to", "body"}, {"type": "typing", "to"}, {"type": "read", "peer"} or
    {"type": "ping"} (every 25 s keeps tunnels and proxies from closing it)."""
    from app.modules.chat.hub import hub

    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=10)
    except Exception:  # noqa: BLE001 - nothing, or not JSON
        await ws.close(code=4401)
        return
    if not isinstance(first, dict) or first.get("type") != "auth":
        await ws.close(code=4401)
        return
    who = await run_in_threadpool(_ws_identity, str(first.get("token") or ""), str(first.get("business_id") or ""))
    if isinstance(who, str):
        await ws.send_json({"type": "error", "message": who})
        await ws.close(code=4401)
        return
    bid, me, _name = who

    def unread() -> int:
        with SessionLocal() as s:
            return service.unread_total(s, bid, me)

    online = {str(u) for u in hub.online(bid)} | {str(me)}
    await ws.send_json({"type": "ready", "me": str(me), "online": sorted(online),
                        "unread": await run_in_threadpool(unread), "server_time": datetime.now(UTC).isoformat()})
    await hub.join(ws, bid, me)
    try:
        while True:
            frame = await ws.receive_json()
            kind = frame.get("type") if isinstance(frame, dict) else None
            if kind == "ping":
                await ws.send_json({"type": "pong"})
            elif kind == "typing":
                with contextlib.suppress(ValueError, TypeError):
                    await hub.send(bid, uuid.UUID(str(frame.get("to"))), {"type": "typing", "from": str(me)})
            elif kind == "send":
                result = await run_in_threadpool(_send_from_socket, bid, me, frame)
                if isinstance(result, str):
                    await ws.send_json({"type": "error", "message": result, "ref": frame.get("ref")})
                else:
                    message, peer_unread = result
                    await hub.deliver(bid, message, {message.recipient_id: peer_unread})
                    await ws.send_json({"type": "sent", "ref": frame.get("ref"), "id": str(message.id)})
            elif kind == "read":
                with contextlib.suppress(ValueError, TypeError):
                    peer = uuid.UUID(str(frame.get("peer")))
                    await run_in_threadpool(_read_from_socket, bid, me, peer)
                    await hub.send(bid, peer, {"type": "read", "by": str(me)})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:  # noqa: BLE001 - bad frame: close this socket only
        log.info("chat socket closed: %s", exc)
    finally:
        await hub.leave(ws, bid, me)


def _member_access(db: Any, bid: uuid.UUID, me: uuid.UUID) -> BusinessAccess:
    from app.modules.access.service import get_business_access
    from app.modules.users.models import User

    user = db.get(User, me)
    assert user is not None
    return get_business_access(db, user, bid)


def _send_from_socket(bid: uuid.UUID, me: uuid.UUID, frame: dict[str, Any]) -> tuple[Any, int] | str:
    from app.core.errors import AppError

    try:
        to = uuid.UUID(str(frame.get("to")))
        body = frame.get("body")
        if body is not None and not isinstance(body, str):
            return "Message must be text"
        with SessionLocal() as db:
            access = _member_access(db, bid, me)
            m = service.send(db, access, to=to, body=body)
            db.commit()
            return m, service.unread_total(db, bid, to)
    except AppError as exc:
        return exc.message
    except (ValueError, TypeError):
        return "Bad message"


def _read_from_socket(bid: uuid.UUID, me: uuid.UUID, peer: uuid.UUID) -> None:
    with SessionLocal() as db:
        service.mark_read(db, _member_access(db, bid, me), peer)
        db.commit()
