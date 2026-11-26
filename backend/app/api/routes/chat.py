"""Chat between salespeople, staff and the owner of a business."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import DB, require
from app.core.errors import ValidationFailed
from app.db.session import SessionLocal
from app.modules.access.service import BusinessAccess
from app.modules.chat import service

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
    return rows


@router.post("/with/{peer_id}/read")
def read(peer_id: uuid.UUID, access: Member, db: DB) -> dict[str, int]:
    n = service.mark_read(db, access, peer_id)
    db.commit()
    return {"marked": n}


@router.post("/messages", status_code=status.HTTP_201_CREATED)
def send(body: SendIn, access: Member, db: DB) -> dict[str, Any]:
    m = service.send(db, access, to=body.to, body=body.body, document_id=body.document_id)
    db.commit()
    return service.out(m, access.user.id)


@router.post("/photos", status_code=status.HTTP_201_CREATED)
async def send_photo(access: Member, db: DB, to: Annotated[uuid.UUID, Form()], file: UploadFile = File(...),
                     caption: Annotated[str | None, Form(max_length=1000)] = None) -> dict[str, Any]:
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise ValidationFailed("Photos up to 15 MB")
    m = await run_in_threadpool(service.send_photo, db, access, to=to, data=data, filename=file.filename or "photo.jpg", caption=caption)
    await run_in_threadpool(db.commit)
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
