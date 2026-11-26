"""WebSocket delivery for chat: messages, "typing…", online status and read receipts.

Each API process keeps the sockets of the people connected to it. A message sent in this process
is pushed at once. Messages saved by another process (several API workers, or the HTTP fallback)
are picked up by a single loop per process that looks for new rows once a second, so they arrive
within a second there too. Typing and online status are shared between people on the same process
(always the case on a single computer).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.db.session import SessionLocal

log = logging.getLogger(__name__)


def _json(obj: Any) -> str:
    return json.dumps(obj, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o))


class Hub:
    def __init__(self) -> None:
        self.sockets: dict[tuple[uuid.UUID, uuid.UUID], set[WebSocket]] = {}  # (business, user) -> sockets
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pump_task: asyncio.Task[None] | None = None
        self.delivered: deque[uuid.UUID] = deque(maxlen=2000)
        self.cursor = datetime.now(UTC)

    # ------------------------------------------------------------------ connections
    def online(self, business_id: uuid.UUID) -> list[uuid.UUID]:
        return [u for (b, u), s in self.sockets.items() if b == business_id and s]

    async def join(self, ws: WebSocket, business_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.loop = asyncio.get_running_loop()
        first = not self.sockets.get((business_id, user_id))
        self.sockets.setdefault((business_id, user_id), set()).add(ws)
        if self.pump_task is None or self.pump_task.done():
            self.cursor = datetime.now(UTC)
            self.pump_task = asyncio.create_task(self._pump())
        if first:
            await self.presence(business_id)

    async def leave(self, ws: WebSocket, business_id: uuid.UUID, user_id: uuid.UUID) -> None:
        s = self.sockets.get((business_id, user_id))
        if s is not None:
            s.discard(ws)
            if not s:
                del self.sockets[(business_id, user_id)]
                await self.presence(business_id)
        if not self.sockets and self.pump_task is not None:
            self.pump_task.cancel()
            self.pump_task = None

    async def send(self, business_id: uuid.UUID, user_id: uuid.UUID, payload: dict[str, Any]) -> None:
        text = _json(payload)
        for ws in list(self.sockets.get((business_id, user_id), ())):
            with contextlib.suppress(Exception):  # a dead socket is removed when its reader ends
                await ws.send_text(text)

    async def presence(self, business_id: uuid.UUID) -> None:
        online = self.online(business_id)
        for u in online:
            await self.send(business_id, u, {"type": "presence", "online": online})

    # ------------------------------------------------------------------ messages
    async def deliver(self, business_id: uuid.UUID, message: Any, unread_for: dict[uuid.UUID, int] | None = None) -> None:
        """Push one saved ChatMessage to its sender's other tabs and to the recipient."""
        from app.modules.chat.service import out

        if message.id in self.delivered:
            return
        self.delivered.append(message.id)
        for uid in {message.sender_id, message.recipient_id}:
            if (business_id, uid) in self.sockets:
                payload: dict[str, Any] = {"type": "message", "message": out(message, uid)}
                if unread_for and uid in unread_for:
                    payload["unread"] = unread_for[uid]
                await self.send(business_id, uid, payload)

    def deliver_threadsafe(self, business_id: uuid.UUID, message: Any, unread: int | None = None) -> None:
        """From a normal (threaded) request: push now on the event loop, if anyone is connected here."""
        if self.loop is None or not self.sockets:
            return
        extra = {message.recipient_id: unread} if unread is not None else None
        with contextlib.suppress(RuntimeError):  # loop closed (shutdown)
            asyncio.run_coroutine_threadsafe(self.deliver(business_id, message, extra), self.loop)

    def read_threadsafe(self, business_id: uuid.UUID, reader: uuid.UUID, peer: uuid.UUID) -> None:
        if self.loop is None or (business_id, peer) not in self.sockets:
            return
        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(self.send(business_id, peer, {"type": "read", "by": reader}), self.loop)

    async def _pump(self) -> None:
        """Pick up messages saved by other processes (once a second, one query for everyone)."""
        from app.modules.chat.models import ChatMessage
        from app.modules.chat.service import unread_total

        while self.sockets:
            await asyncio.sleep(1)
            since = self.cursor

            def fetch(since: datetime = since) -> list[tuple[Any, int]]:
                with SessionLocal() as s:
                    rows = list(s.scalars(select(ChatMessage).where(ChatMessage.created_at > since)
                                          .order_by(ChatMessage.created_at).limit(500)))
                    fresh = [m for m in rows if m.id not in self.delivered
                             and ((m.business_id, m.recipient_id) in self.sockets or (m.business_id, m.sender_id) in self.sockets)]
                    return [(m, unread_total(s, m.business_id, m.recipient_id)) for m in fresh] + [(rows[-1], -1)] if rows else []

            try:
                found = await run_in_threadpool(fetch)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive (database restart…)
                log.warning("chat pump: %s", exc)
                continue
            for m, unread in found:
                if unread == -1:
                    self.cursor = max(self.cursor, m.created_at)
                    continue
                await self.deliver(m.business_id, m, {m.recipient_id: unread})


hub = Hub()
