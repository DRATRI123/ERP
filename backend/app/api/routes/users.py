"""Owner-only, account-wide user administration (across all businesses)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select

from app.api.deps import DB, OwnerPrincipal
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import ResetTokenOut, UserOut, UserUpdateIn
from app.core.errors import PermissionDenied
from app.modules.auth import service as auth
from app.modules.auth.models import TokenPurpose
from app.modules.users import service
from app.modules.users.models import User, UserStatus

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserOut])
def list_users(
    _: OwnerPrincipal, db: DB, params: Annotated[PageParams, Depends(page_params)],
    q: str | None = Query(None, max_length=100), status: UserStatus | None = None,
) -> Page[UserOut]:
    stmt = select(User).order_by(User.full_name)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(User.email.like(like), User.full_name.ilike(like)))
    if status is not None:
        stmt = stmt.where(User.status == status)
    rows, total = paginate(db, stmt, params)
    return Page(items=[UserOut.of(u) for u in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, _: OwnerPrincipal, db: DB) -> UserOut:
    return UserOut.of(service.get_user(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdateIn, p: OwnerPrincipal, db: DB) -> UserOut:
    user = service.update_profile(db, actor=p.user, user=service.get_user(db, user_id),
                                  full_name=body.full_name, phone=body.phone)
    db.commit()
    return UserOut.of(user)


@router.post("/{user_id}/suspend", response_model=UserOut)
def suspend_user(user_id: uuid.UUID, p: OwnerPrincipal, db: DB) -> UserOut:
    user = service.set_status(db, actor=p.user, user=service.get_user(db, user_id),
                              status=UserStatus.SUSPENDED)
    db.commit()
    return UserOut.of(user)


@router.post("/{user_id}/reactivate", response_model=UserOut)
def reactivate_user(user_id: uuid.UUID, p: OwnerPrincipal, db: DB) -> UserOut:
    user = service.set_status(db, actor=p.user, user=service.get_user(db, user_id),
                              status=UserStatus.ACTIVE)
    db.commit()
    return UserOut.of(user)


@router.post("/{user_id}/password-reset", response_model=ResetTokenOut)
def issue_password_reset(user_id: uuid.UUID, p: OwnerPrincipal, db: DB) -> ResetTokenOut:
    """Create a one-time reset token (shown once) for the owner to hand to the user."""
    user = service.get_user(db, user_id)
    if user.owner:
        raise PermissionDenied("Use change-password or the CLI for the owner account")
    token, expires = auth.issue_token(db, user=user, purpose=TokenPurpose.PASSWORD_RESET,
                                      created_by=p.user)
    db.commit()
    return ResetTokenOut(reset_token=token, expires_at=expires)
