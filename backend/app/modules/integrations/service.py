"""Integration lifecycle: connect, configure, rotate credentials, test, disconnect, OAuth."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.models import Account, AccountSubtype
from app.modules.audit import service as audit
from app.modules.businesses.models import Branch
from app.modules.integrations.base import (
    REGISTRY,
    CallLog,
    IntegrationError,
    ProviderSpec,
)
from app.modules.integrations.models import Integration, IntegrationStatus
from app.modules.integrations.providers import commerce, gateways, messaging
from app.modules.inventory.models import Warehouse

TESTS = {
    "smtp": messaging.smtp_test, "whatsapp_cloud": messaging.whatsapp_test, "msg91": messaging.msg91_test,
    "razorpay": gateways.razorpay_test, "cashfree": gateways.cashfree_test,
    "google": commerce.google_test, "shopify": commerce.shopify_test,
}


def spec(provider: str) -> ProviderSpec:
    if provider not in REGISTRY:
        raise ValidationFailed(f"Unknown provider {provider!r}", details={"available": sorted(REGISTRY)})
    return REGISTRY[provider]


def credentials(integration: Integration) -> dict[str, Any]:
    if not integration.credentials_encrypted:
        raise IntegrationError(f"{integration.name} has no credentials (disconnected?)")
    return decrypt_json(integration.credentials_encrypted)


def webhook_url(integration: Integration) -> str | None:
    if not spec(integration.provider).webhook:
        return None
    return f"{get_settings().public_base_url.rstrip('/')}/api/v1/hooks/{integration.provider}/{integration.id}"


log = logging.getLogger(__name__)
CALLBACK_PATH = "/api/v1/hooks/google/oauth/callback"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _trusted_origin(origin: str | None) -> str | None:
    """The address the ERP is opened at, if this server answers to it. Next.js (local runs) and
    Caddy (production) both pass /api/v1 to the API, so the callback works on that same origin."""
    if not origin:
        return None
    p = urlsplit(origin.strip())
    if p.scheme not in ("http", "https") or not p.hostname or p.path not in ("", "/") or p.query or p.username:
        return None
    s = get_settings()
    trusted = {urlsplit(s.public_base_url).hostname, *(h.lower() for h in s.allowed_hosts),
               *(urlsplit(o).hostname for o in s.cors_origins)}
    host = p.hostname.lower()
    if host in LOCAL_HOSTS or host in trusted:
        return f"{p.scheme}://{p.netloc}".lower()
    return None


def oauth_origin(origin: str | None = None) -> str:
    return _trusted_origin(origin) or get_settings().public_base_url.rstrip("/")


def oauth_redirect_uri(origin: str | None = None) -> str:
    return f"{oauth_origin(origin)}{CALLBACK_PATH}"


def oauth_setup(origin: str | None) -> dict[str, Any]:
    """What to enter in Google Cloud Console → Credentials → OAuth client (Web application)."""
    base = oauth_origin(origin)
    p = urlsplit(base)
    host = (p.hostname or "").lower()
    warnings = []
    if p.scheme != "https" and host not in LOCAL_HOSTS:
        warnings.append("Google accepts plain http only for localhost. Open the ERP on https (your domain) "
                        "or on http://localhost on this computer to connect Google.")
    if host.replace(".", "").isdigit() and host not in LOCAL_HOSTS:
        warnings.append("Google does not accept an IP address here. Use a domain name, or localhost on this computer.")
    if host.endswith((".local", ".lan", ".internal")):
        warnings.append("Google needs a public domain name (or localhost); local network names are refused.")
    if origin and _trusted_origin(origin) is None:
        warnings.append(f"This server is not set up for {origin}; the address from ERP_PUBLIC_BASE_URL is shown. "
                        "Add the domain to ERP_ALLOWED_HOSTS / ERP_PUBLIC_BASE_URL.")
    return {"javascript_origins": [base], "redirect_uris": [f"{base}{CALLBACK_PATH}"], "warnings": warnings,
            "scopes": list(commerce.SCOPES),
            "apis": ["Gmail API", "Google Drive API"]}


def get_integration(db: Session, access: BusinessAccess, iid: uuid.UUID) -> Integration:
    i = db.get(Integration, iid)
    if i is None or i.business_id != access.business.id or i.removed_at is not None:
        raise NotFound("Integration not found")
    return i


def active_for(db: Session, business_id: uuid.UUID, capability: str,
               integration_id: uuid.UUID | None = None) -> Integration:
    """The connected integration to use for a capability (explicit, or the only/first one)."""
    stmt = select(Integration).where(Integration.business_id == business_id,
                                     Integration.status == IntegrationStatus.CONNECTED)
    if integration_id:
        stmt = stmt.where(Integration.id == integration_id)
    rows = [i for i in db.scalars(stmt.order_by(Integration.created_at))
            if capability in REGISTRY[i.provider].capabilities]
    if not rows:
        raise ValidationFailed(f"No connected integration can {capability.replace('_', ' ')}; "
                               "connect one under Integrations first")
    return rows[0]


def _validate(db: Session, access: BusinessAccess, sp: ProviderSpec, config: dict[str, Any],
              creds: dict[str, Any], *, partial: bool) -> None:
    for f in sp.fields:
        bucket = creds if f.secret else config
        if f.required and not partial and not str(bucket.get(f.name) or "").strip():
            raise ValidationFailed(f"{f.label} is required", details={"field": f.name})
    unknown = (set(config) | set(creds)) - {f.name for f in sp.fields}
    if unknown:
        raise ValidationFailed("Unknown fields", details={"fields": sorted(unknown)})
    misplaced = [f.name for f in sp.fields if (f.secret and f.name in config) or (not f.secret and f.name in creds)]
    if misplaced:
        raise ValidationFailed("Secrets go in credentials and settings in config", details={"fields": misplaced})
    if config.get("settlement_account_id"):
        a = db.get(Account, uuid.UUID(str(config["settlement_account_id"])))
        if a is None or a.business_id != access.business.id or a.subtype not in (AccountSubtype.BANK, AccountSubtype.CASH):
            raise ValidationFailed("settlement_account_id must be one of your bank or cash accounts")
    if config.get("warehouse_id"):
        w = db.get(Warehouse, uuid.UUID(str(config["warehouse_id"])))
        if w is None or w.business_id != access.business.id:
            raise ValidationFailed("warehouse_id is not a warehouse of this business")
    if config.get("branch_id"):
        b = db.get(Branch, uuid.UUID(str(config["branch_id"])))
        if b is None or b.business_id != access.business.id:
            raise ValidationFailed("branch_id is not a branch of this business")


def _record(db: Session, access: BusinessAccess, action: str, i: Integration, changes: dict | None = None) -> None:
    # Field names only — secret values are never written to the audit log.
    audit.record(db, action=action, entity_type="integration", entity_id=i.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"provider": i.provider, "name": i.name, **(changes or {})})


def connect(db: Session, access: BusinessAccess, *, provider: str, name: str, config: dict[str, Any],
            creds: dict[str, Any]) -> Integration:
    access.require("integrations.manage")
    sp = spec(provider)
    _validate(db, access, sp, config, creds, partial=False)
    same = db.scalar(select(Integration).where(Integration.business_id == access.business.id, Integration.name == name))
    if same is not None:
        if same.status == IntegrationStatus.DISCONNECTED:
            raise Conflict(f"{name!r} is already in the list (disconnected). Use Reconnect on it, or Remove it first.")
        raise Conflict(f"An integration named {name!r} already exists")
    i = Integration(business_id=access.business.id, provider=provider, name=name, config=config,
                    credentials_encrypted=encrypt_json(creds), credentials_rotated_at=datetime.now(UTC),
                    status=IntegrationStatus.PENDING if sp.oauth else IntegrationStatus.CONNECTED,
                    created_by_id=access.user.id)
    db.add(i)
    db.flush()
    _record(db, access, "integration.connected", i, {"config_fields": sorted(config),
                                                    "credential_fields": sorted(creds)})
    return i


def update_config(db: Session, access: BusinessAccess, i: Integration, config: dict[str, Any]) -> Integration:
    access.require("integrations.manage")
    _validate(db, access, spec(i.provider), config, {}, partial=True)
    before = dict(i.config)
    i.config = {**i.config, **config}
    _record(db, access, "integration.configured", i, {"changed": sorted(k for k in config if before.get(k) != config[k])})
    return i


def rotate_credentials(db: Session, access: BusinessAccess, i: Integration, creds: dict[str, Any]) -> Integration:
    """Replace some or all secrets (e.g. a regenerated API key). Unsent fields are kept."""
    access.require("integrations.manage")
    _validate(db, access, spec(i.provider), {}, creds, partial=True)
    current = credentials(i) if i.credentials_encrypted else {}
    i.credentials_encrypted = encrypt_json({**current, **creds})
    i.credentials_rotated_at = datetime.now(UTC)
    if i.status == IntegrationStatus.DISCONNECTED:
        i.status = IntegrationStatus.PENDING if spec(i.provider).oauth else IntegrationStatus.CONNECTED
    _record(db, access, "integration.credentials_rotated", i, {"fields": sorted(creds)})
    return i


def test_connection(db: Session, access: BusinessAccess, i: Integration) -> tuple[bool, str]:
    access.require("integrations.manage")
    i.last_tested_at = datetime.now(UTC)
    try:
        with CallLog(db, i, "test") as log:  # records failure (and last_error) on exception
            message = TESTS[i.provider](i.config, credentials(i))
            log.detail = message
    except IntegrationError as exc:
        if i.status == IntegrationStatus.CONNECTED:
            i.status = IntegrationStatus.ERROR
        return False, exc.message
    if i.status == IntegrationStatus.ERROR:
        i.status = IntegrationStatus.CONNECTED
    return True, message


def disconnect(db: Session, access: BusinessAccess, i: Integration) -> Integration:
    """Stop using the integration and destroy its stored secrets. History stays. Works from any
    state (pending, error, connected) and again on an already-disconnected one. For Google the
    refresh token is also revoked at Google (best effort: a failure there never blocks this)."""
    access.require("integrations.manage")
    if i.provider == "google" and i.credentials_encrypted:
        try:
            token = decrypt_json(i.credentials_encrypted).get("refresh_token")
            if token:
                commerce.google_revoke(token)
        except Exception as exc:  # noqa: BLE001 - offline, already revoked, or unreadable: still disconnect
            log.warning("Google token revoke failed for integration %s: %s", i.id, exc)
    i.status = IntegrationStatus.DISCONNECTED
    i.credentials_encrypted = None
    i.last_error = None
    _record(db, access, "integration.disconnected", i)
    return i


def remove(db: Session, access: BusinessAccess, i: Integration) -> str:
    """Delete the integration (disconnecting it first). If messages, payment links or synced
    orders still point at it, it is hidden instead and its name freed, so history stays intact."""
    access.require("integrations.manage")
    from sqlalchemy import exists

    from app.modules.integrations.models import GatewayPayment, OutboundMessage, PaymentLink

    if i.status != IntegrationStatus.DISCONNECTED or i.credentials_encrypted:
        disconnect(db, access, i)
    used = any(db.scalar(select(exists().where(m.integration_id == i.id)))
               for m in (OutboundMessage, PaymentLink, GatewayPayment))
    _record(db, access, "integration.removed", i, {"kept_for_history": used})
    if used:
        i.removed_at = datetime.now(UTC)
        i.name = f"{i.name[:60]} · removed {i.id.hex[:6]}"
        return "hidden"
    db.delete(i)
    db.flush()
    return "deleted"


# ---------------------------------------------------------------------------- Google OAuth


def _state_token(i: Integration, redirect_uri: str) -> str:
    claims = {"iid": str(i.id), "aud": "google-oauth", "exp": datetime.now(UTC) + timedelta(minutes=15),
              "ru": redirect_uri}
    return jwt.encode(claims, get_settings().secret_key, algorithm="HS256")


def google_consent_url(db: Session, access: BusinessAccess, i: Integration, origin: str | None = None) -> str:
    access.require("integrations.manage")
    if i.provider != "google":
        raise ValidationFailed("Only Google integrations use OAuth consent")
    if i.status == IntegrationStatus.DISCONNECTED or not i.credentials_encrypted:
        raise Conflict("This integration is disconnected — use Reconnect to enter the client ID and secret again")
    ru = oauth_redirect_uri(origin)
    return commerce.google_auth_url(credentials(i), redirect_uri=ru, state=_state_token(i, ru))


def decode_state(state: str) -> dict[str, Any]:
    try:
        return jwt.decode(state, get_settings().secret_key, algorithms=["HS256"], audience="google-oauth")
    except jwt.PyJWTError as exc:
        raise ValidationFailed("The authorisation link expired or was tampered with; start again") from exc


def google_callback(db: Session, *, code: str, state: str) -> Integration:
    claims = decode_state(state)
    i = db.get(Integration, uuid.UUID(claims["iid"]))
    if i is None or i.provider != "google":
        raise NotFound("Integration not found")
    if i.status == IntegrationStatus.DISCONNECTED or not i.credentials_encrypted:
        raise Conflict("This integration was disconnected meanwhile; reconnect it and try again")
    with CallLog(db, i, "oauth_exchange"):
        creds = credentials(i)
        tok = commerce.google_exchange_code(creds, code=code, redirect_uri=claims.get("ru") or oauth_redirect_uri())
        i.credentials_encrypted = encrypt_json({**creds, "refresh_token": tok["refresh_token"]})
    i.status = IntegrationStatus.CONNECTED
    i.credentials_rotated_at = datetime.now(UTC)
    audit.record(db, action="integration.oauth_authorised", entity_type="integration", entity_id=i.id,
                 business_id=i.business_id, actor_id=None, changes={"provider": "google"})
    return i
