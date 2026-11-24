"""Password hashing (Argon2id), access tokens (JWT) and opaque secret tokens."""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

# OWASP-recommended Argon2id parameters (m=19 MiB, t=2, p=1) are the floor; use more memory.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
# Verifying against this when the user does not exist keeps timing uniform (no user enumeration).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "erp"


_hash_slots: threading.BoundedSemaphore | None = None


def _slots() -> threading.BoundedSemaphore:
    """Each Argon2 operation takes 64 MiB and a full core: bound how many run at once so a burst
    of sign-ins (or a password-guessing attack) cannot exhaust memory. Extra callers wait."""
    global _hash_slots
    if _hash_slots is None:
        n = get_settings().password_hash_concurrency or (os.cpu_count() or 2)
        _hash_slots = threading.BoundedSemaphore(max(1, n))
    return _hash_slots


def hash_password(password: str) -> str:
    with _slots():
        return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        with _slots():
            return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_secret_token() -> str:
    """256-bit URL-safe random token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Fast hash for high-entropy tokens (refresh/invite/API keys). Not for passwords."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID) -> tuple[str, int]:
    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": JWT_ISSUER,
        "sub": str(user_id),
        "sid": str(session_id),
        "typ": "access",
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.secret_key, algorithm=JWT_ALGORITHM), int(
        ttl.total_seconds()
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises ``jwt.PyJWTError`` on any problem (expiry, signature, wrong type)."""
    claims = jwt.decode(
        token,
        get_settings().secret_key,
        algorithms=[JWT_ALGORITHM],
        issuer=JWT_ISSUER,
        options={"require": ["exp", "iat", "sub", "sid", "typ"]},
    )
    if claims.get("typ") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return claims


def password_fingerprint(password_hash: str | None) -> str:
    return hashlib.sha256(f"mfa:{password_hash or ''}".encode()).hexdigest()[:24]


def create_mfa_token(user_id: uuid.UUID, password_hash: str | None) -> tuple[str, int]:
    """Proof that the password step succeeded, valid for a few minutes, usable only for the
    second-factor step. Bound to the password version, so a password change voids it."""
    settings = get_settings()
    ttl = timedelta(minutes=settings.mfa_token_ttl_minutes)
    now = datetime.now(UTC)
    claims = {"iss": JWT_ISSUER, "sub": str(user_id), "typ": "mfa", "iat": now, "exp": now + ttl,
              "jti": uuid.uuid4().hex,
              "pwd": password_fingerprint(password_hash)}
    return jwt.encode(claims, settings.secret_key, algorithm=JWT_ALGORITHM), int(ttl.total_seconds())


def decode_mfa_token(token: str) -> dict[str, Any]:
    claims = jwt.decode(token, get_settings().secret_key, algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER,
                        options={"require": ["exp", "iat", "sub", "typ"]})
    if claims.get("typ") != "mfa":
        raise jwt.InvalidTokenError("not an MFA token")
    return claims
