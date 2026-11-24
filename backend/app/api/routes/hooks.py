"""Public inbound endpoints for providers: webhooks and the Google OAuth callback.

These are unauthenticated by design. Every request is authenticated by the provider's
signature over the raw body (or the signed OAuth state); anything else is rejected with 401.
Responses reveal nothing about the business.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.api.deps import DB
from app.core.errors import NotAuthenticated, NotFound
from app.modules.integrations import outbox, sync
from app.modules.integrations import service as svc
from app.modules.integrations.base import IntegrationError, same
from app.modules.integrations.models import Integration, IntegrationStatus
from app.modules.integrations.providers import messaging

router = APIRouter(prefix="/hooks", tags=["webhooks"])
MAX_BODY = 1024 * 1024


def _integration(db: DB, provider: str, integration_id: uuid.UUID) -> Integration:
    i = db.get(Integration, integration_id)
    if i is None or i.provider != provider or i.status == IntegrationStatus.DISCONNECTED or not i.credentials_encrypted:
        raise NotFound("Not found")
    return i


async def _body(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_BODY:
        raise NotAuthenticated("Payload too large")
    return body


@router.get("/google/oauth/callback", response_class=HTMLResponse)
def google_callback(db: DB, code: str = Query(max_length=2000), state: str = Query(max_length=2000)) -> HTMLResponse:
    svc.google_callback(db, code=code, state=state)
    db.commit()
    return HTMLResponse("<h3>Google is connected.</h3><p>You can close this window.</p>")


@router.get("/whatsapp_cloud/{integration_id}", response_class=PlainTextResponse)
def whatsapp_verify(integration_id: uuid.UUID, db: DB, request: Request) -> PlainTextResponse:
    """Meta's subscription handshake: echo hub.challenge if the verify token matches."""
    i = _integration(db, "whatsapp_cloud", integration_id)
    q = request.query_params
    if q.get("hub.mode") != "subscribe" or not same(q.get("hub.verify_token"), svc.credentials(i)["verify_token"]):
        raise NotAuthenticated("Verification failed")
    return PlainTextResponse(q.get("hub.challenge", ""))


@router.post("/{provider}/{integration_id}")
async def receive(provider: str, integration_id: uuid.UUID, request: Request, db: DB) -> dict:
    i = _integration(db, provider, integration_id)
    body = await _body(request)
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        if provider == "whatsapp_cloud":
            if not messaging.whatsapp_verify_signature(svc.credentials(i), body, headers.get("x-hub-signature-256")):
                raise IntegrationError("Invalid signature")
            result = f"{outbox.apply_whatsapp_statuses(db, i, messaging.whatsapp_statuses(body))} updated"
        elif provider in ("razorpay", "cashfree"):
            result = sync.handle_gateway_webhook(db, i, body, headers)
        elif provider == "shopify":
            result = sync.handle_shopify_webhook(db, i, body, headers)
        else:
            raise NotFound("Not found")
    except IntegrationError as exc:
        db.rollback()
        sync._log_inbound(db, i, "webhook_rejected", False, exc.message)
        db.commit()
        raise NotAuthenticated("Webhook rejected") from exc
    db.commit()
    return {"ok": True, "result": result}
