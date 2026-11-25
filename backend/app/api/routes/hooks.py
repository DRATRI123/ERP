"""Public inbound endpoints for providers: webhooks and the Google OAuth callback.

These are unauthenticated by design. Every request is authenticated by the provider's
signature over the raw body (or the signed OAuth state); anything else is rejected with 401.
Responses reveal nothing about the business.
"""

from __future__ import annotations

import html
import json
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.api.deps import DB
from app.core.errors import AppError, NotAuthenticated, NotFound
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


def _oauth_page(ok: bool, message: str, back: str | None) -> HTMLResponse:
    title = "Google is connected" if ok else "Google was not connected"
    link = f'<p><a href="{html.escape(back)}">Back to Integrations</a></p>' if back else ""
    colour = "#15803d" if ok else "#b91c1c"
    script = f'<script>setTimeout(function(){{location.href={json.dumps(back)}}},2500)</script>' if ok and back else ""
    body = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><body style="font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem">'
            f'<h2 style="color:{colour}">{title}</h2><p>{html.escape(message)}</p>{link}{script}</body>')
    return HTMLResponse(body, status_code=200 if ok else 400)


@router.get("/google/oauth/callback", response_class=HTMLResponse)
def google_callback(db: DB, code: str | None = Query(None, max_length=2000), state: str | None = Query(None, max_length=2000),
                    error: str | None = Query(None, max_length=200)) -> HTMLResponse:
    """Where Google sends the browser back. Always answers with a readable page, never raw JSON."""
    back = None
    if state:
        try:
            ru = svc.decode_state(state).get("ru") or svc.oauth_redirect_uri()
            back = ru.removesuffix(svc.CALLBACK_PATH) + "/integrations"
        except AppError:
            pass
    if error or not code or not state:
        reason = {"access_denied": "You cancelled on Google's screen, or this Google account is not a test user "
                                   "of the app (OAuth consent screen → Test users)."}.get(error or "", error or "Google sent no code.")
        return _oauth_page(False, reason, back)
    try:
        svc.google_callback(db, code=code, state=state)
        db.commit()
    except (AppError, IntegrationError) as exc:
        db.rollback()
        msg = getattr(exc, "message", str(exc))
        if "redirect_uri_mismatch" in msg or "invalid_grant" in msg:
            msg += " — check that the Authorized redirect URI in Google Cloud matches the one shown in the ERP exactly."
        return _oauth_page(False, msg, back)
    return _oauth_page(True, "Gmail sending and Drive backups can now be used. Taking you back…", back)


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
