"""User account lifecycle. There is no public sign-up: the owner is created from the CLI and
everyone else is invited by the owner or a business administrator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.security import hash_password
from app.modules.audit import service as audit
from app.modules.auth.models import UserSession
from app.modules.users.models import User, UserStatus


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_password_policy(password: str, *, email: str | None = None) -> None:
    settings = get_settings()
    problems: list[str] = []
    if len(password) < settings.password_min_length:
        problems.append(f"must be at least {settings.password_min_length} characters")
    if len(password) > 128:
        problems.append("must be at most 128 characters")
    if len(set(password)) < 4:
        problems.append("is too repetitive")
    local = normalize_email(email).split("@")[0] if email else ""
    if len(local) >= 4 and local in password.lower():
        problems.append("must not contain your email name")
    if problems:
        raise ValidationFailed("Password " + ", ".join(problems))


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return user


def get_owner(db: Session) -> User | None:
    return db.scalar(select(User).where(User.is_owner.is_(True)))


def create_owner(db: Session, *, email: str, full_name: str, password: str) -> User:
    if get_owner(db) is not None:
        raise Conflict("An owner account already exists")
    if get_by_email(db, email) is not None:
        raise Conflict("A user with this email already exists")
    validate_password_policy(password, email=email)
    now = datetime.now(UTC)
    owner = User(
        email=normalize_email(email),
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        is_owner=True,
        status=UserStatus.ACTIVE,
        password_changed_at=now,
    )
    db.add(owner)
    db.flush()
    audit.record(db, action="owner.created", entity_type="user", entity_id=owner.id,
                 actor_id=owner.id, changes={"email": owner.email})
    return owner


def create_invited_user(
    db: Session, *, email: str, full_name: str, phone: str | None, created_by: User
) -> User:
    if get_by_email(db, email) is not None:
        raise Conflict("A user with this email already exists")
    user = User(
        email=normalize_email(email),
        full_name=full_name.strip(),
        phone=phone,
        status=UserStatus.INVITED,
    )
    db.add(user)
    db.flush()
    audit.record(db, action="user.invited", entity_type="user", entity_id=user.id,
                 actor_id=created_by.id, changes={"email": user.email})
    return user


def revoke_all_sessions(
    db: Session, user_id: uuid.UUID, reason: str, *, except_session: uuid.UUID | None = None
) -> None:
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    if except_session is not None:
        stmt = stmt.where(UserSession.id != except_session)
    db.execute(stmt)


def set_status(db: Session, *, actor: User, user: User, status: UserStatus) -> User:
    """Owner-only: suspend or reactivate an account across all businesses."""
    if not actor.owner:
        raise PermissionDenied("Only the owner can suspend or reactivate accounts")
    if user.owner:
        raise PermissionDenied("The owner account cannot be suspended")
    if status == UserStatus.ACTIVE and user.password_hash is None:
        raise ValidationFailed("This user has not accepted their invitation yet")
    old = user.status
    user.status = status
    if status == UserStatus.SUSPENDED:
        revoke_all_sessions(db, user.id, "account_suspended")
    audit.record(db, action=f"user.{status.value}", entity_type="user", entity_id=user.id,
                 actor_id=actor.id, changes={"status": {"from": old.value, "to": status.value}})
    return user


def update_profile(
    db: Session, *, actor: User, user: User, full_name: str | None, phone: str | None
) -> User:
    before = audit.snapshot(user, ["full_name", "phone"])
    if full_name is not None:
        user.full_name = full_name.strip()
    if phone is not None:
        user.phone = phone or None
    audit.record(db, action="user.updated", entity_type="user", entity_id=user.id,
                 actor_id=actor.id,
                 changes=audit.diff(before, audit.snapshot(user, ["full_name", "phone"])))
    return user
