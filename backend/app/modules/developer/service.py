"""API applications, keys, authentication and OAuth 2.0 client-credentials tokens."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.core.security import JWT_ALGORITHM, JWT_ISSUER, hash_token
from app.modules.access.service import BusinessAccess, get_business_access
from app.modules.audit import service as audit
from app.modules.businesses.models import Branch, Business
from app.modules.developer.catalog import SCOPES, permissions_for
from app.modules.developer.models import ApiClient, ApiKey
from app.modules.users.models import User, UserStatus

KEY_PREFIX = "erp_"
TOKEN_TTL = timedelta(minutes=15)
ROTATION_GRACE = timedelta(hours=24)


@dataclass(frozen=True)
class ApiIdentity:
    client: ApiClient
    key_prefix: str | None  # None when authenticated with an OAuth access token


# ---------------------------------------------------------------------------- management


def _manageable_businesses(db: Session, user: User) -> set[str]:
    """Businesses where the user may manage developer access."""
    if user.owner:
        return {str(b) for b in db.scalars(select(Business.id))}
    from app.modules.access.models import Membership, MembershipStatus
    from app.modules.access.service import effective_permissions

    return {str(m.business_id) for m in db.scalars(select(Membership).where(
        Membership.user_id == user.id, Membership.status == MembershipStatus.ACTIVE))
        if "developer.manage" in effective_permissions(m)}


def _validate(db: Session, user: User, data: dict[str, Any]) -> None:
    unknown = set(data.get("scopes", [])) - set(SCOPES)
    if unknown:
        raise ValidationFailed("Unknown scopes", details={"unknown": sorted(unknown), "available": sorted(SCOPES)})
    allowed = _manageable_businesses(db, user)
    want = set(data.get("business_ids", []))
    if not want:
        raise ValidationFailed("Allow at least one business")
    if want - allowed:
        raise PermissionDenied("You can only grant access to businesses where you manage developer access")
    for bid in want:  # a non-owner cannot hand out scopes beyond their own permissions
        access = get_business_access(db, user, uuid.UUID(bid))
        beyond = permissions_for(data.get("scopes", [])) - access.permissions
        if beyond:
            raise PermissionDenied("These scopes exceed your own permissions",
                                   details={"business_id": bid, "permissions": sorted(beyond)})
    if data.get("branch_ids") is not None:
        found = {str(b.id) for b in db.scalars(select(Branch).where(
            Branch.id.in_([uuid.UUID(x) for x in data["branch_ids"]]),
            Branch.business_id.in_([uuid.UUID(x) for x in want])))}
        if found != set(data["branch_ids"]):
            raise ValidationFailed("branch_ids must belong to the allowed businesses")


def list_clients(db: Session, user: User) -> list[ApiClient]:
    stmt = select(ApiClient).order_by(ApiClient.name)
    if not user.owner:
        stmt = stmt.where(ApiClient.owner_user_id == user.id)
    return list(db.scalars(stmt))


def get_client(db: Session, user: User, cid: uuid.UUID) -> ApiClient:
    c = db.get(ApiClient, cid)
    if c is None or (not user.owner and c.owner_user_id != user.id):
        raise NotFound("API application not found")
    return c


def create_client(db: Session, user: User, data: dict[str, Any]) -> ApiClient:
    _validate(db, user, data)
    c = ApiClient(owner_user_id=user.id, **data)
    db.add(c)
    db.flush()
    audit.record(db, action="api_client.created", entity_type="api_client", entity_id=c.id, actor_id=user.id,
                 changes={"name": c.name, "scopes": c.scopes, "business_ids": c.business_ids})
    return c


def update_client(db: Session, user: User, c: ApiClient, data: dict[str, Any]) -> ApiClient:
    merged = {"scopes": c.scopes, "business_ids": c.business_ids, "branch_ids": c.branch_ids, **data}
    _validate(db, user, merged)
    before = {k: getattr(c, k) for k in data}
    for k, v in data.items():
        setattr(c, k, v)
    audit.record(db, action="api_client.updated", entity_type="api_client", entity_id=c.id, actor_id=user.id,
                 changes={k: {"from": before[k], "to": v} for k, v in data.items() if before[k] != v})
    return c


def create_key(db: Session, user: User, c: ApiClient, *, label: str | None,
               expires_in_days: int | None) -> tuple[ApiKey, str]:
    """Returns the key record and the full secret, which is shown exactly once."""
    prefix = secrets.token_hex(6)
    raw = f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    key = ApiKey(client_id=c.id, prefix=prefix, key_hash=hash_token(raw), label=label, created_by_id=user.id,
                 expires_at=datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None)
    db.add(key)
    db.flush()
    audit.record(db, action="api_key.created", entity_type="api_client", entity_id=c.id, actor_id=user.id,
                 changes={"prefix": prefix, "label": label, "expires_at": key.expires_at})
    return key, raw


def get_key(db: Session, c: ApiClient, kid: uuid.UUID) -> ApiKey:
    k = db.get(ApiKey, kid)
    if k is None or k.client_id != c.id:
        raise NotFound("API key not found")
    return k


def revoke_key(db: Session, user: User, c: ApiClient, k: ApiKey, *, at: datetime | None = None) -> ApiKey:
    k.revoked_at = at or datetime.now(UTC)
    audit.record(db, action="api_key.revoked", entity_type="api_client", entity_id=c.id, actor_id=user.id,
                 changes={"prefix": k.prefix, "effective": k.revoked_at})
    return k


def rotate_key(db: Session, user: User, c: ApiClient, k: ApiKey) -> tuple[ApiKey, str]:
    """New key now; the old one keeps working for 24 hours so deployments can switch over."""
    if k.revoked_at is not None:
        raise ValidationFailed("This key is already revoked")
    new, raw = create_key(db, user, c, label=k.label, expires_in_days=None)
    new.expires_at = k.expires_at
    revoke_key(db, user, c, k, at=datetime.now(UTC) + ROTATION_GRACE)
    return new, raw


# ---------------------------------------------------------------------------- authentication


def _active_client(db: Session, client_id: uuid.UUID) -> ApiClient:
    c = db.get(ApiClient, client_id)
    if c is None or not c.is_active:
        raise NotAuthenticated("API application is disabled", code="invalid_api_key")
    owner = db.get(User, c.owner_user_id)
    if owner is None or owner.status != UserStatus.ACTIVE:
        raise NotAuthenticated("The user this application acts for is not active", code="invalid_api_key")
    return c


def authenticate_key(db: Session, raw: str, *, touch: bool = True) -> tuple[ApiClient, ApiKey]:
    if not raw.startswith(KEY_PREFIX):
        raise NotAuthenticated("Invalid API key", code="invalid_api_key")
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_token(raw)))
    now = datetime.now(UTC)
    if key is None or (key.revoked_at and key.revoked_at <= now) or (key.expires_at and key.expires_at <= now):
        raise NotAuthenticated("Invalid, revoked or expired API key", code="invalid_api_key")
    client = _active_client(db, key.client_id)
    if touch and (key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1)):
        key.last_used_at = now  # throttled: at most one write per key per minute
    return client, key


def issue_token(db: Session, *, client_id: uuid.UUID, client_secret: str, scope: str | None) -> dict[str, Any]:
    """OAuth 2.0 client-credentials grant: exchange a key for a 15-minute access token,
    optionally narrowed to fewer scopes."""
    client, key = authenticate_key(db, client_secret)
    if client.id != client_id:
        raise NotAuthenticated("Invalid client credentials", code="invalid_client")
    scopes = client.scopes if not scope else scope.split()
    if set(scopes) - set(client.scopes):
        raise PermissionDenied("Requested scopes exceed the application's scopes", code="invalid_scope")
    now = datetime.now(UTC)
    token = jwt.encode({"iss": JWT_ISSUER, "sub": str(client.id), "typ": "api", "kid": key.prefix,
                        "scope": " ".join(scopes), "iat": now, "exp": now + TOKEN_TTL},
                       get_settings().secret_key, algorithm=JWT_ALGORITHM)
    return {"access_token": token, "token_type": "bearer", "expires_in": int(TOKEN_TTL.total_seconds()),
            "scope": " ".join(scopes)}


def authenticate_token(db: Session, claims: dict[str, Any]) -> tuple[ApiClient, list[str], str]:
    client = _active_client(db, uuid.UUID(claims["sub"]))
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == claims.get("kid")))
    now = datetime.now(UTC)
    if key is None or key.client_id != client.id or (key.revoked_at and key.revoked_at <= now):
        raise NotAuthenticated("The key behind this token was revoked", code="invalid_token")
    scopes = [s for s in claims.get("scope", "").split() if s in client.scopes]  # can only narrow
    return client, scopes, key.prefix


def business_access(db: Session, client: ApiClient, scopes: list[str], business_id: uuid.UUID) -> BusinessAccess:
    if str(business_id) not in client.business_ids:
        raise NotFound("Business not found")
    owner = db.get(User, client.owner_user_id)
    assert owner is not None
    human = get_business_access(db, owner, business_id)  # the creator's access *now*
    from app.modules.businesses.models import BusinessStatus

    if human.business.status != BusinessStatus.ACTIVE:
        raise NotFound("Business not found")
    branch_ids = frozenset(uuid.UUID(b) for b in client.branch_ids) if client.branch_ids is not None else None
    if branch_ids is not None and not human.all_branches:
        branch_ids &= human.branch_ids
    return BusinessAccess(
        user=owner, business=human.business, membership=human.membership,
        permissions=permissions_for(scopes) & human.permissions,
        all_branches=branch_ids is None and human.all_branches,
        branch_ids=branch_ids if branch_ids is not None else human.branch_ids,
        api_client_id=client.id)
