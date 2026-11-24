"""FastAPI dependencies: DB session, authenticated principal, business-scoped access.

Transactions: route handlers call ``db.commit()`` explicitly once their work succeeds. Anything
not committed is rolled back when the session closes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import jwt
from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import NotAuthenticated, PermissionDenied
from app.core.security import decode_access_token
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


DB = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login")


@dataclass(frozen=True)
class Principal:
    user: User
    session_id: uuid.UUID


def _authenticate(db: Session, creds: HTTPAuthorizationCredentials | None) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise NotAuthenticated("Authentication required")
    try:
        claims = decode_access_token(creds.credentials)
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


def get_principal(
    db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Principal:
    return _authenticate(db, creds)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_owner(p: CurrentPrincipal) -> Principal:
    if not p.user.owner:
        raise PermissionDenied("Only the owner can perform this action")
    return p


OwnerPrincipal = Annotated[Principal, Depends(require_owner)]


def get_access(
    db: DB, p: CurrentPrincipal, business_id: Annotated[uuid.UUID, Path()]
) -> BusinessAccess:
    return get_business_access(db, p.user, business_id)


Access = Annotated[BusinessAccess, Depends(get_access)]


def require(*codes: str) -> Callable[..., BusinessAccess]:
    """Dependency factory: resolve business access and demand every listed permission."""

    def _dep(access: Access) -> BusinessAccess:
        access.require(*codes)
        return access

    _dep.__name__ = f"require_{'_'.join(c.replace('.', '_') for c in codes)}"
    return _dep
