"""Developer platform: API applications and keys, usage and logs, OAuth tokens, webhooks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import case, func, select

from app.api.deps import DB, CurrentPrincipal, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.core.errors import NotFound, PermissionDenied, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.developer import service as dev
from app.modules.developer import webhooks as wh
from app.modules.developer.catalog import EVENT_TYPES, SCOPES
from app.modules.developer.models import (
    ApiClient,
    ApiKey,
    ApiRequestLog,
    EndpointStatus,
    WebhookDelivery,
    WebhookEndpoint,
)

router = APIRouter(tags=["developer"])
ManageHooks = Annotated[BusinessAccess, Depends(require("developer.manage"))]


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    business_ids: list[str] = Field(min_length=1)
    branch_ids: list[str] | None = None
    scopes: list[str] = Field(min_length=1)
    rate_limit_per_minute: int = Field(120, ge=1, le=6000)


class ClientUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    business_ids: list[str] | None = None
    branch_ids: list[str] | None = None
    scopes: list[str] | None = None
    rate_limit_per_minute: int | None = Field(None, ge=1, le=6000)
    is_active: bool | None = None


class KeyIn(BaseModel):
    label: str | None = Field(None, max_length=60)
    expires_in_days: int | None = Field(None, ge=1, le=3650)


class EndpointIn(BaseModel):
    url: HttpUrl
    events: list[str] = Field(min_length=1, max_length=50)
    description: str | None = Field(None, max_length=200)


class EndpointUpdateIn(BaseModel):
    url: HttpUrl | None = None
    events: list[str] | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=200)
    status: EndpointStatus | None = None


def _require_developer(p: CurrentPrincipal, db: DB) -> None:
    if not p.user.owner and not dev._manageable_businesses(db, p.user):
        raise PermissionDenied("You need developer access (developer.manage) in at least one business")


def _client_out(db: DB, c: ApiClient) -> dict[str, Any]:
    keys = db.scalars(select(ApiKey).where(ApiKey.client_id == c.id).order_by(ApiKey.created_at.desc()))
    now = datetime.now(UTC)
    return {"id": c.id, "name": c.name, "description": c.description, "business_ids": c.business_ids,
            "branch_ids": c.branch_ids, "scopes": c.scopes, "rate_limit_per_minute": c.rate_limit_per_minute,
            "is_active": c.is_active, "created_at": c.created_at,
            "keys": [{"id": k.id, "prefix": k.prefix, "label": k.label, "created_at": k.created_at,
                      "expires_at": k.expires_at, "last_used_at": k.last_used_at, "revoked_at": k.revoked_at,
                      "active": not (k.revoked_at and k.revoked_at <= now) and not (k.expires_at and k.expires_at <= now)}
                     for k in keys]}


# ---------------------------------------------------------------------------- catalogs & OAuth


@router.get("/developer/scopes")
def scopes() -> list[dict[str, str]]:
    return [{"scope": s, "description": d} for s, (d, _) in SCOPES.items()]


@router.get("/developer/events")
def event_types() -> list[dict[str, str]]:
    return [{"type": t, "description": d} for t, d in EVENT_TYPES.items()]


@router.post("/oauth/token")
def oauth_token(db: DB, grant_type: Annotated[str, Form()], client_id: Annotated[uuid.UUID, Form()],
                client_secret: Annotated[str, Form()], scope: Annotated[str | None, Form()] = None) -> dict:
    """OAuth 2.0 client credentials: ``client_secret`` is one of the application's API keys.
    Returns a 15-minute bearer token, optionally limited to fewer scopes."""
    if grant_type != "client_credentials":
        raise ValidationFailed("Only grant_type=client_credentials is supported", code="unsupported_grant_type")
    out = dev.issue_token(db, client_id=client_id, client_secret=client_secret, scope=scope)
    db.commit()
    return out


# ---------------------------------------------------------------------------- applications & keys


@router.get("/developer/apps")
def list_apps(p: CurrentPrincipal, db: DB) -> list[dict[str, Any]]:
    _require_developer(p, db)
    return [_client_out(db, c) for c in dev.list_clients(db, p.user)]


@router.post("/developer/apps", status_code=status.HTTP_201_CREATED)
def create_app(body: ClientIn, p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    _require_developer(p, db)
    c = dev.create_client(db, p.user, body.model_dump())
    db.commit()
    return _client_out(db, c)


@router.patch("/developer/apps/{client_id}")
def update_app(client_id: uuid.UUID, body: ClientUpdateIn, p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    c = dev.update_client(db, p.user, dev.get_client(db, p.user, client_id), body.model_dump(exclude_unset=True))
    db.commit()
    return _client_out(db, c)


@router.post("/developer/apps/{client_id}/keys", status_code=status.HTTP_201_CREATED)
def create_key(client_id: uuid.UUID, body: KeyIn, p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    """The full key is returned once. Store it in your application's secret store."""
    c = dev.get_client(db, p.user, client_id)
    k, raw = dev.create_key(db, p.user, c, label=body.label, expires_in_days=body.expires_in_days)
    db.commit()
    return {"id": k.id, "prefix": k.prefix, "key": raw, "expires_at": k.expires_at,
            "warning": "This key is shown only once."}


@router.post("/developer/apps/{client_id}/keys/{key_id}/rotate", status_code=status.HTTP_201_CREATED)
def rotate_key(client_id: uuid.UUID, key_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    c = dev.get_client(db, p.user, client_id)
    new, raw = dev.rotate_key(db, p.user, c, dev.get_key(db, c, key_id))
    db.commit()
    return {"id": new.id, "prefix": new.prefix, "key": raw,
            "old_key_valid_until": (datetime.now(UTC) + dev.ROTATION_GRACE).isoformat(),
            "warning": "This key is shown only once."}


@router.post("/developer/apps/{client_id}/keys/{key_id}/revoke")
def revoke_key(client_id: uuid.UUID, key_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    c = dev.get_client(db, p.user, client_id)
    dev.revoke_key(db, p.user, c, dev.get_key(db, c, key_id))
    db.commit()
    return _client_out(db, c)


@router.get("/developer/apps/{client_id}/usage")
def usage(client_id: uuid.UUID, p: CurrentPrincipal, db: DB, days: int = 14) -> dict[str, Any]:
    c = dev.get_client(db, p.user, client_id)
    since = datetime.now(UTC) - timedelta(days=min(max(days, 1), 90))
    day = func.substr(func.cast(ApiRequestLog.created_at, ApiRequestLog.path.type), 1, 10)
    rows = db.execute(select(day, func.count(), func.sum(case((ApiRequestLog.status >= 400, 1), else_=0)),
                             func.avg(ApiRequestLog.duration_ms))
                      .where(ApiRequestLog.client_id == c.id, ApiRequestLog.created_at >= since)
                      .group_by(day).order_by(day)).all()
    return {"client_id": c.id, "days": [{"date": d, "requests": n, "errors": int(e or 0),
                                         "avg_ms": round(float(a or 0), 1)} for d, n, e, a in rows],
            "total_requests": sum(r[1] for r in rows), "total_errors": sum(int(r[2] or 0) for r in rows)}


@router.get("/developer/apps/{client_id}/logs")
def request_logs(client_id: uuid.UUID, p: CurrentPrincipal, db: DB,
                 params: Annotated[PageParams, Depends(page_params)], errors_only: bool = False) -> Page[dict]:
    c = dev.get_client(db, p.user, client_id)
    stmt = select(ApiRequestLog).where(ApiRequestLog.client_id == c.id).order_by(ApiRequestLog.created_at.desc())
    if errors_only:
        stmt = stmt.where(ApiRequestLog.status >= 400)
    rows, total = paginate(db, stmt, params)
    return Page(items=[{"method": r.method, "path": r.path, "status": r.status, "error_code": r.error_code,
                        "duration_ms": r.duration_ms, "key_prefix": r.key_prefix, "ip_address": r.ip_address,
                        "request_id": r.request_id, "created_at": r.created_at} for r in rows],
                total=total, limit=params.limit, offset=params.offset)


# ---------------------------------------------------------------------------- webhooks


def _endpoint(db: DB, access: BusinessAccess, eid: uuid.UUID) -> WebhookEndpoint:
    ep = db.get(WebhookEndpoint, eid)
    if ep is None or ep.business_id != access.business.id:
        raise NotFound("Webhook endpoint not found")
    return ep


def _ep_out(ep: WebhookEndpoint) -> dict[str, Any]:
    return {"id": ep.id, "url": ep.url, "description": ep.description, "events": ep.events,
            "status": ep.status.value, "disabled_reason": ep.disabled_reason,
            "consecutive_failures": ep.consecutive_failures, "created_at": ep.created_at}


@router.get("/businesses/{business_id}/webhooks")
def list_endpoints(access: ManageHooks, db: DB) -> list[dict[str, Any]]:
    return [_ep_out(e) for e in db.scalars(select(WebhookEndpoint).where(
        WebhookEndpoint.business_id == access.business.id).order_by(WebhookEndpoint.created_at))]


@router.post("/businesses/{business_id}/webhooks", status_code=status.HTTP_201_CREATED)
def create_endpoint(body: EndpointIn, access: ManageHooks, db: DB) -> dict[str, Any]:
    ep, secret = wh.create_endpoint(db, access, url=str(body.url), events_=body.events, description=body.description)
    db.commit()
    return _ep_out(ep) | {"secret": secret, "warning": "The signing secret is shown only once."}


@router.patch("/businesses/{business_id}/webhooks/{endpoint_id}")
def update_endpoint(endpoint_id: uuid.UUID, body: EndpointUpdateIn, access: ManageHooks, db: DB) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if "url" in data:
        data["url"] = str(data["url"])
    ep = wh.update_endpoint(db, access, _endpoint(db, access, endpoint_id), data)
    db.commit()
    return _ep_out(ep)


@router.post("/businesses/{business_id}/webhooks/{endpoint_id}/rotate-secret")
def rotate_secret(endpoint_id: uuid.UUID, access: ManageHooks, db: DB) -> dict[str, Any]:
    secret = wh.rotate_secret(db, access, _endpoint(db, access, endpoint_id))
    db.commit()
    return {"secret": secret, "note": "Deliveries carry signatures from both secrets for 24 hours."}


@router.post("/businesses/{business_id}/webhooks/{endpoint_id}/test", status_code=status.HTTP_202_ACCEPTED)
def test_endpoint(endpoint_id: uuid.UUID, access: ManageHooks, db: DB) -> dict[str, Any]:
    d = wh.send_test(db, access, _endpoint(db, access, endpoint_id))
    db.commit()
    return {"delivery_id": d.id, "status": d.status.value}


@router.get("/businesses/{business_id}/webhooks/{endpoint_id}/deliveries")
def deliveries(endpoint_id: uuid.UUID, access: ManageHooks, db: DB,
               params: Annotated[PageParams, Depends(page_params)], failed_only: bool = False) -> Page[dict]:
    ep = _endpoint(db, access, endpoint_id)
    stmt = select(WebhookDelivery).where(WebhookDelivery.endpoint_id == ep.id).order_by(WebhookDelivery.created_at.desc())
    if failed_only:
        stmt = stmt.where(WebhookDelivery.status == "failed")
    rows, total = paginate(db, stmt, params)
    return Page(items=[{"id": d.id, "event_id": d.event_id, "event_type": d.event_type, "status": d.status.value,
                        "attempts": d.attempts, "response_status": d.response_status, "error": d.error,
                        "response_body": d.response_body, "duration_ms": d.duration_ms,
                        "next_attempt_at": d.next_attempt_at, "last_attempt_at": d.last_attempt_at}
                       for d in rows], total=total, limit=params.limit, offset=params.offset)


@router.post("/businesses/{business_id}/webhooks/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: uuid.UUID, access: ManageHooks, db: DB) -> dict[str, Any]:
    d = db.get(WebhookDelivery, delivery_id)
    if d is None or _endpoint(db, access, d.endpoint_id) is None:
        raise NotFound("Delivery not found")
    wh.retry_delivery(db, access, d)
    db.commit()
    return {"id": d.id, "status": d.status.value}
