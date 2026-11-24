"""Outgoing webhooks: endpoint management, fan-out from the outbox, signed delivery, retries.

Delivery contract (documented for receivers in docs/api.md):

* ``POST`` JSON ``{"id", "type", "created_at", "business_id", "data"}``.
* Headers ``ERP-Event-Id``, ``ERP-Event-Type`` and ``ERP-Signature: t=<unix>,v1=<hex>`` where
  ``v1 = HMAC-SHA256(secret, f"{t}.{raw_body}")``. During a secret rotation a second ``v1`` is
  sent, signed with the previous secret. Receivers should reject timestamps older than 5 minutes
  and de-duplicate on ``id``.
* Any 2xx is success. Otherwise retried after 1 min, 5 min, 30 min, 2 h, 6 h, 12 h, then marked
  failed. An endpoint failing 20 deliveries in a row is disabled automatically.

Webhooks are sent from the worker, after the change has committed: a failing receiver can never
roll back an invoice or payment.
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document
from app.modules.developer.catalog import EVENT_TYPES, matches
from app.modules.developer.models import (
    DeliveryStatus,
    EndpointStatus,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.modules.events import service as events
from app.modules.events.models import OutboxEvent
from app.modules.integrations.base import hmac_hex, http_client

BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2),
           timedelta(hours=6), timedelta(hours=12)]
DISABLE_AFTER = 20
ROTATION_GRACE = timedelta(hours=24)

# Tests replace this; production resolves DNS.
resolver = socket.getaddrinfo


def _check_url(url: str) -> None:
    """HTTPS only, and never an internal address (SSRF protection): a webhook must not be able
    to make this server call its own database, metadata service or LAN."""
    p = urlparse(url)
    if p.scheme != "https":
        raise ValidationFailed("Webhook URLs must use https://")
    if not p.hostname or p.username or p.password:
        raise ValidationFailed("Invalid webhook URL")
    try:
        infos = resolver(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValidationFailed(f"Cannot resolve {p.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                or ip.is_unspecified:
            raise ValidationFailed("Webhook URLs must point to a public internet address")


def _check_events(events_: list[str]) -> None:
    for e in events_:
        family = e[:-2] if e.endswith(".*") else None
        if e != "*" and e not in EVENT_TYPES and not (family and any(t.startswith(family + ".") for t in EVENT_TYPES)):
            raise ValidationFailed(f"Unknown event {e!r}", details={"available": sorted(EVENT_TYPES)})


def create_endpoint(db: Session, access: BusinessAccess, *, url: str, events_: list[str],
                    description: str | None) -> tuple[WebhookEndpoint, str]:
    access.require("developer.manage")
    _check_url(url)
    _check_events(events_)
    secret = "whsec_" + secrets.token_urlsafe(32)
    ep = WebhookEndpoint(business_id=access.business.id, url=url, events=sorted(set(events_)),
                         description=description, secret_encrypted=encrypt_json({"s": secret}),
                         status=EndpointStatus.ACTIVE, created_by_id=access.user.id)
    db.add(ep)
    db.flush()
    audit.record(db, action="webhook.created", entity_type="webhook_endpoint", entity_id=ep.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"url": url, "events": ep.events})
    return ep, secret


def update_endpoint(db: Session, access: BusinessAccess, ep: WebhookEndpoint, data: dict[str, Any]) -> WebhookEndpoint:
    access.require("developer.manage")
    if "url" in data:
        _check_url(data["url"])
    if "events" in data:
        _check_events(data["events"])
        data["events"] = sorted(set(data["events"]))
    if data.get("status") == EndpointStatus.ACTIVE:
        ep.consecutive_failures, ep.disabled_reason = 0, None
    elif data.get("status") == EndpointStatus.DISABLED:
        ep.disabled_reason = "Disabled by user"
    for k, v in data.items():
        setattr(ep, k, v)
    audit.record(db, action="webhook.updated", entity_type="webhook_endpoint", entity_id=ep.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={k: str(v) for k, v in data.items()})
    return ep


def rotate_secret(db: Session, access: BusinessAccess, ep: WebhookEndpoint) -> str:
    access.require("developer.manage")
    secret = "whsec_" + secrets.token_urlsafe(32)
    ep.previous_secret_encrypted = ep.secret_encrypted
    ep.previous_secret_expires_at = datetime.now(UTC) + ROTATION_GRACE
    ep.secret_encrypted = encrypt_json({"s": secret})
    audit.record(db, action="webhook.secret_rotated", entity_type="webhook_endpoint", entity_id=ep.id,
                 business_id=access.business.id, actor_id=access.user.id)
    return secret


def send_test(db: Session, access: BusinessAccess, ep: WebhookEndpoint) -> WebhookDelivery:
    access.require("developer.manage")
    ev = events.emit(db, "webhook.test", aggregate_type="webhook_endpoint", aggregate_id=ep.id,
                     business_id=access.business.id, payload={"message": "Test event from your ERP"})
    db.flush()
    ev.webhooks_dispatched_at = datetime.now(UTC)  # only this endpoint receives it
    d = WebhookDelivery(endpoint_id=ep.id, event_id=ev.id, event_type=ev.event_type,
                        status=DeliveryStatus.PENDING, next_attempt_at=datetime.now(UTC))
    db.add(d)
    db.flush()
    return d


def signature_header(secrets_: list[str], timestamp: int, body: bytes) -> str:
    signed = f"{timestamp}.".encode() + body
    return ",".join([f"t={timestamp}", *(f"v1={hmac_hex(s, signed)}" for s in secrets_)])


# ---------------------------------------------------------------------------- worker side


def fan_out(db: Session, *, limit: int = 500) -> int:
    """Create a delivery per matching active endpoint for each new outbox event."""
    evs = list(db.scalars(select(OutboxEvent).where(OutboxEvent.webhooks_dispatched_at.is_(None))
                          .order_by(OutboxEvent.created_at).limit(limit)))
    if not evs:
        db.rollback()  # never leave the worker's transaction (and SQLite's lock) open
        return 0
    endpoints = list(db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.status == EndpointStatus.ACTIVE)))
    now = datetime.now(UTC)
    created = 0
    for ev in evs:
        for ep in endpoints:
            if ep.business_id == ev.business_id and ev.event_type != "webhook.test" and matches(ep.events, ev.event_type):
                db.add(WebhookDelivery(endpoint_id=ep.id, event_id=ev.id, event_type=ev.event_type,
                                       status=DeliveryStatus.PENDING, next_attempt_at=now))
                created += 1
        ev.webhooks_dispatched_at = now
    db.commit()
    return created


def _payload(ev: OutboxEvent) -> bytes:
    return json.dumps({"id": str(ev.id), "type": ev.event_type, "created_at": ev.created_at.isoformat(),
                       "business_id": str(ev.business_id) if ev.business_id else None, "data": ev.payload},
                      separators=(",", ":"), default=str).encode()


def deliver_due(db: Session, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    now = now or datetime.now(UTC)
    ids = list(db.scalars(select(WebhookDelivery.id).where(
        WebhookDelivery.status == DeliveryStatus.PENDING, WebhookDelivery.next_attempt_at <= now)
        .order_by(WebhookDelivery.next_attempt_at).limit(limit)))
    stats = {"succeeded": 0, "retrying": 0, "failed": 0}
    for did in ids:
        d = db.scalar(select(WebhookDelivery).where(WebhookDelivery.id == did,
                                                    WebhookDelivery.status == DeliveryStatus.PENDING)
                      .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if d is None:
            continue
        ep = db.get(WebhookEndpoint, d.endpoint_id)
        ev = db.get(OutboxEvent, d.event_id)
        if ep.status != EndpointStatus.ACTIVE:
            d.status, d.error, d.next_attempt_at = DeliveryStatus.FAILED, "Endpoint disabled", None
            stats["failed"] += 1
            db.commit()
            continue
        body = _payload(ev)
        keys = [decrypt_json(ep.secret_encrypted)["s"]]
        if ep.previous_secret_encrypted and ep.previous_secret_expires_at and ep.previous_secret_expires_at > now:
            keys.append(decrypt_json(ep.previous_secret_encrypted)["s"])
        ts = int(now.timestamp())
        headers = {"Content-Type": "application/json", "User-Agent": "ERP-Webhooks/1",
                   "ERP-Event-Id": str(ev.id), "ERP-Event-Type": ev.event_type,
                   "ERP-Signature": signature_header(keys, ts, body)}
        d.attempts += 1
        d.last_attempt_at = now
        start = time.perf_counter()
        ok = False
        try:
            _check_url(ep.url)  # DNS may have changed since the endpoint was saved
            with http_client(follow_redirects=False) as c:
                resp = c.post(ep.url, content=body, headers=headers, timeout=10.0)
            d.response_status, d.response_body = resp.status_code, resp.text[:500]
            ok = 200 <= resp.status_code < 300
            d.error = None if ok else f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001 - network errors, SSRF refusal, timeouts
            d.response_status, d.error = None, str(getattr(exc, "message", exc))[:500]
        d.duration_ms = int((time.perf_counter() - start) * 1000)
        if ok:
            d.status, d.next_attempt_at = DeliveryStatus.SUCCEEDED, None
            ep.consecutive_failures = 0
            stats["succeeded"] += 1
        else:
            ep.consecutive_failures += 1
            if d.attempts > len(BACKOFF):
                d.status, d.next_attempt_at = DeliveryStatus.FAILED, None
                stats["failed"] += 1
            else:
                d.next_attempt_at = now + BACKOFF[d.attempts - 1]
                stats["retrying"] += 1
            if ep.consecutive_failures >= DISABLE_AFTER:
                ep.status = EndpointStatus.DISABLED
                ep.disabled_reason = f"Disabled after {DISABLE_AFTER} consecutive failed deliveries"
        db.commit()
    db.rollback()  # end the read transaction when nothing was due
    return stats


def retry_delivery(db: Session, access: BusinessAccess, d: WebhookDelivery) -> WebhookDelivery:
    access.require("developer.manage")
    if d.status == DeliveryStatus.PENDING:
        raise ValidationFailed("This delivery is still pending")
    d.status, d.next_attempt_at, d.attempts = DeliveryStatus.PENDING, datetime.now(UTC), 0
    return d


def emit_overdue(db: Session, *, today: datetime | None = None) -> int:
    """Daily: one invoice.overdue event per unpaid invoice past its due date."""
    day = (today or datetime.now(UTC)).date()
    docs = list(db.scalars(select(Document).where(
        Document.doc_type == DocType.SALES_INVOICE, Document.status == DocStatus.FINAL,
        Document.balance_due > 0, Document.due_date < day, Document.overdue_notified_at.is_(None)).limit(1000)))
    for d in docs:
        events.emit(db, "invoice.overdue", aggregate_type="document", aggregate_id=d.id, business_id=d.business_id,
                    payload={"id": d.id, "number": d.number, "party_id": d.party_id, "due_date": d.due_date,
                             "balance_due": d.balance_due, "days_overdue": (day - d.due_date).days})
        d.overdue_notified_at = datetime.now(UTC)
    db.commit()
    return len(docs)

