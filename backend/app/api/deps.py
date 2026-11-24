"""FastAPI dependencies: DB session, authenticated principal, business-scoped access.

Transactions: route handlers call ``db.commit()`` explicitly once their work succeeds. Anything
not committed is rolled back when the session closes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import jwt
from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotAuthenticated, PermissionDenied, TooManyRequests
from app.core.security import JWT_ALGORITHM, JWT_ISSUER, decode_access_token
from app.db.session import SessionLocal
from app.modules.access.service import BusinessAccess, get_business_access
from app.modules.auth.models import UserSession
from app.modules.users.models import User, UserStatus


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# scope="function": the session closes (rolling back anything uncommitted) as soon as the
# endpoint returns — before the response is sent — so no transaction outlives its request,
# and middleware writing afterwards (API logs, idempotency records) never waits on its lock.
DB = Annotated[Session, Depends(get_db, scope="function")]

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login")


@dataclass(frozen=True)
class Principal:
    """Who is calling: a signed-in user (session) or an API application acting for a user."""

    user: User
    session_id: uuid.UUID | None = None
    api_client: Any = None  # ApiClient when the call comes from an API application
    api_scopes: tuple[str, ...] = ()
    api_key_prefix: str | None = None

    @property
    def is_api(self) -> bool:
        return self.api_client is not None


def _authenticate_api(db: Session, request: Request, token: str) -> Principal:
    from app.core.ratelimit import limiter
    from app.modules.developer import service as dev

    if token.startswith(dev.KEY_PREFIX):
        client, key = dev.authenticate_key(db, token)
        scopes, prefix = list(client.scopes), key.prefix
    else:
        try:
            claims = jwt.decode(token, get_settings().secret_key, algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER,
                                options={"require": ["exp", "sub", "typ"]})
        except jwt.PyJWTError as exc:
            raise NotAuthenticated("Invalid or expired access token", code="invalid_token") from exc
        client, scopes, prefix = dev.authenticate_token(db, claims)
    request.state.api_client_id, request.state.api_key_prefix = client.id, prefix
    allowed, remaining, retry_after = limiter.hit(str(client.id), client.rate_limit_per_minute)
    request.state.rate_limit = (client.rate_limit_per_minute, remaining)
    if not allowed:
        raise TooManyRequests(f"Rate limit of {client.rate_limit_per_minute} requests per minute exceeded",
                              code="rate_limited", headers={"Retry-After": str(retry_after)})
    db.commit()  # persist last_used_at; the request itself opens its own transaction
    owner = db.get(User, client.owner_user_id)
    return Principal(user=owner, api_client=client, api_scopes=tuple(scopes), api_key_prefix=prefix)


def _authenticate(db: Session, request: Request, creds: HTTPAuthorizationCredentials | None) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise NotAuthenticated("Authentication required")
    token = creds.credentials
    if token.startswith("erp_"):
        return _authenticate_api(db, request, token)
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise NotAuthenticated("Invalid or expired access token", code="invalid_token") from exc
    if unverified.get("typ") == "api":  # OAuth client-credentials token (verified inside)
        return _authenticate_api(db, request, token)
    try:
        claims = decode_access_token(token)
        user_id = uuid.UUID(claims["sub"])
        session_id = uuid.UUID(claims["sid"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise NotAuthenticated("Invalid or expired access token", code="invalid_token") from exc

    # Checking the session on every request makes logout, suspension and password changes
    # take effect immediately rather than when the access token expires.
    session = db.get(UserSession, session_id)
    if (
        session is None
        or session.user_id != user_id
        or session.revoked_at is not None
        or session.expires_at <= datetime.now(UTC)
    ):
        raise NotAuthenticated("Session has ended; please sign in again", code="invalid_token")
    user = db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise NotAuthenticated("This account is not active", code="account_inactive")
    return Principal(user=user, session_id=session_id)


def get_any_principal(
    db: DB, request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Principal:
    """A user session, an API key, or an OAuth access token."""
    return _authenticate(db, request, creds)


AnyPrincipal = Annotated[Principal, Depends(get_any_principal)]


def get_principal(p: AnyPrincipal) -> Principal:
    """Signed-in users only: account, user, developer and owner endpoints refuse API keys."""
    if p.is_api:
        raise PermissionDenied("This endpoint is for signed-in users, not API keys", code="user_required")
    return p


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_owner(p: CurrentPrincipal) -> Principal:
    if not p.user.owner:
        raise PermissionDenied("Only the owner can perform this action")
    return p


OwnerPrincipal = Annotated[Principal, Depends(require_owner)]


def get_access(
    db: DB, p: AnyPrincipal, business_id: Annotated[uuid.UUID, Path()]
) -> BusinessAccess:
    if p.is_api:
        from app.modules.developer.service import business_access

        return business_access(db, p.api_client, list(p.api_scopes), business_id)
    return get_business_access(db, p.user, business_id)


Access = Annotated[BusinessAccess, Depends(get_access)]


def require(*codes: str) -> Callable[..., BusinessAccess]:
    """Dependency factory: resolve business access and demand every listed permission."""

    def _dep(access: Access) -> BusinessAccess:
        access.require(*codes)
        return access

    _dep.__name__ = f"require_{'_'.join(c.replace('.', '_') for c in codes)}"
    return _dep


def require_any(*codes: str) -> Callable[..., BusinessAccess]:
    """Dependency factory: demand at least one of the listed permissions. For endpoints whose
    exact permission depends on the request (e.g. sales vs purchase documents): the precise
    check still happens in the service, but members holding none of the candidates are refused
    before the request body is even parsed."""

    def _dep(access: Access) -> BusinessAccess:
        if not any(access.can(c) for c in codes):
            access.require(codes[0])  # raises the standard PermissionDenied
        return access

    _dep.__name__ = f"require_any_{'_'.join(c.replace('.', '_') for c in codes)}"
    return _dep


def require_business_owner(access: Access) -> BusinessAccess:
    if not access.is_owner:
        from app.core.errors import PermissionDenied

        raise PermissionDenied("Only the owner can perform this action")
    return access


OwnerAccess = Annotated[BusinessAccess, Depends(require_business_owner)]
