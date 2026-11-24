"""Login, refresh-token rotation, logout, password changes, invitations and resets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import client_ip_var, user_agent_var
from app.core.errors import NotAuthenticated, TooManyRequests, ValidationFailed
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_secret_token,
    password_needs_rehash,
    verify_password,
)
from app.modules.audit import service as audit
from app.modules.auth.models import LoginEvent, OneTimeToken, TokenPurpose, UserSession
from app.modules.users import service as users
from app.modules.users.models import User, UserStatus

INVALID_CREDENTIALS = "Invalid email or password"


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    session_id: uuid.UUID


def _now() -> datetime:
    return datetime.now(UTC)


def _log_event(
    db: Session, *, email: str, user: User | None, event: str, success: bool, reason: str | None = None
) -> None:
    db.add(
        LoginEvent(
            user_id=user.id if user else None,
            email=email[:254],
            event=event,
            success=success,
            failure_reason=reason,
            ip_address=client_ip_var.get(),
            user_agent=(user_agent_var.get() or "")[:300] or None,
        )
    )


def _open_session(db: Session, user: User) -> IssuedTokens:
    refresh = new_secret_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh),
        user_agent=(user_agent_var.get() or "")[:300] or None,
        ip_address=client_ip_var.get(),
        expires_at=_now() + timedelta(days=get_settings().refresh_token_ttl_days),
    )
    db.add(session)
    db.flush()
    access, ttl = create_access_token(user.id, session.id)
    return IssuedTokens(access, ttl, refresh, session.id)


def login(db: Session, *, email: str, password: str) -> IssuedTokens:
    """Authenticate. On failure the failure is committed before raising, so lockout counters
    survive the request's rollback."""
    settings = get_settings()
    email_n = users.normalize_email(email)
    user = users.get_by_email(db, email_n)
    now = _now()

    def fail(reason: str, exc: Exception) -> None:
        _log_event(db, email=email_n, user=user, event="login", success=False, reason=reason)
        db.commit()
        raise exc

    if user is not None and user.locked_until is not None and user.locked_until > now:
        verify_password(None, password)  # constant-ish timing
        fail("locked", TooManyRequests(
            "Too many failed attempts. Try again later.", code="account_locked"))

    ok = verify_password(user.password_hash if user else None, password)
    if user is None or not ok:
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_login_count = 0
        fail("bad_credentials" if user else "unknown_user", NotAuthenticated(INVALID_CREDENTIALS))
    assert user is not None

    if user.status != UserStatus.ACTIVE:
        fail(f"status_{user.status.value}", NotAuthenticated(
            "This account is not active", code="account_inactive"))

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash or ""):
        user.password_hash = hash_password(password)
    tokens = _open_session(db, user)
    _log_event(db, email=email_n, user=user, event="login", success=True)
    return tokens


def refresh(db: Session, refresh_token: str) -> IssuedTokens:
    token_hash = hash_token(refresh_token)
    now = _now()
    session = db.scalar(select(UserSession).where(UserSession.refresh_token_hash == token_hash))
    if session is None:
        reused = db.scalar(
            select(UserSession).where(UserSession.previous_token_hash == token_hash)
        )
        if reused is not None and reused.revoked_at is None:
            # A rotated token was replayed: assume theft and kill the session.
            reused.revoked_at = now
            reused.revoked_reason = "refresh_token_reuse"
            user = db.get(User, reused.user_id)
            _log_event(db, email=user.email if user else "", user=user,
                       event="refresh_reuse", success=False, reason="token_reuse")
            db.commit()
        raise NotAuthenticated("Invalid refresh token")
    if session.revoked_at is not None or session.expires_at <= now:
        raise NotAuthenticated("Session has ended; please sign in again")
    user = db.get(User, session.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise NotAuthenticated("This account is not active", code="account_inactive")

    new_refresh = new_secret_token()
    session.previous_token_hash = token_hash
    session.refresh_token_hash = hash_token(new_refresh)
    session.last_used_at = now
    access, ttl = create_access_token(user.id, session.id)
    return IssuedTokens(access, ttl, new_refresh, session.id)


def logout(db: Session, *, user: User, session_id: uuid.UUID) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason="logout")
    )
    _log_event(db, email=user.email, user=user, event="logout", success=True)


def revoke_session(db: Session, *, user: User, session_id: uuid.UUID) -> bool:
    result = db.execute(
        update(UserSession)
        .where(
            UserSession.id == session_id,
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now(), revoked_reason="revoked_by_user")
    )
    return bool(result.rowcount)


def list_sessions(db: Session, user: User) -> list[UserSession]:
    return list(
        db.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > _now(),
            )
            .order_by(UserSession.last_used_at.desc())
        )
    )


def _set_password(db: Session, user: User, new_password: str) -> None:
    users.validate_password_policy(new_password, email=user.email)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = _now()
    user.failed_login_count = 0
    user.locked_until = None


def change_password(
    db: Session, *, user: User, session_id: uuid.UUID, current: str, new: str
) -> None:
    if not verify_password(user.password_hash, current):
        raise ValidationFailed("Current password is incorrect", code="wrong_password")
    if current == new:
        raise ValidationFailed("New password must differ from the current password")
    _set_password(db, user, new)
    users.revoke_all_sessions(db, user.id, "password_changed", except_session=session_id)
    audit.record(db, action="user.password_changed", entity_type="user", entity_id=user.id,
                 actor_id=user.id)


# ---------------------------------------------------------------------------- one-time tokens


def issue_token(
    db: Session, *, user: User, purpose: TokenPurpose, created_by: User | None
) -> tuple[str, datetime]:
    """Create a single-use token (invalidating earlier unused ones). Returns the raw token —
    it is never stored and must be delivered to the user out of band."""
    settings = get_settings()
    now = _now()
    db.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.user_id == user.id,
            OneTimeToken.purpose == purpose,
            OneTimeToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    ttl = (
        timedelta(hours=settings.invite_token_ttl_hours)
        if purpose == TokenPurpose.INVITE
        else timedelta(hours=settings.password_reset_ttl_hours)
    )
    raw = new_secret_token()
    db.add(OneTimeToken(
        user_id=user.id, purpose=purpose, token_hash=hash_token(raw),
        created_by_id=created_by.id if created_by else None, expires_at=now + ttl,
    ))
    audit.record(db, action=f"user.{purpose.value}_issued", entity_type="user", entity_id=user.id,
                 actor_id=created_by.id if created_by else None)
    return raw, now + ttl


def _consume_token(db: Session, raw: str, purpose: TokenPurpose) -> User:
    tok = db.scalar(select(OneTimeToken).where(OneTimeToken.token_hash == hash_token(raw)))
    if tok is None or tok.purpose != purpose or tok.used_at is not None or tok.expires_at <= _now():
        raise ValidationFailed("This link is invalid or has expired", code="invalid_token")
    user = db.get(User, tok.user_id)
    if user is None or user.status == UserStatus.SUSPENDED:
        raise ValidationFailed("This link is invalid or has expired", code="invalid_token")
    tok.used_at = _now()
    return user


def accept_invite(db: Session, *, token: str, password: str) -> User:
    user = _consume_token(db, token, TokenPurpose.INVITE)
    if user.status != UserStatus.INVITED:
        raise ValidationFailed("This invitation has already been accepted", code="invalid_token")
    _set_password(db, user, password)
    user.status = UserStatus.ACTIVE
    audit.record(db, action="user.invite_accepted", entity_type="user", entity_id=user.id,
                 actor_id=user.id)
    return user


def reset_password(db: Session, *, token: str, password: str) -> User:
    user = _consume_token(db, token, TokenPurpose.PASSWORD_RESET)
    _set_password(db, user, password)
    users.revoke_all_sessions(db, user.id, "password_reset")
    audit.record(db, action="user.password_reset", entity_type="user", entity_id=user.id,
                 actor_id=user.id)
    return user
