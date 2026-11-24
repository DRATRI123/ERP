from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import (
    ChangePasswordIn,
    LoginEventOut,
    LoginIn,
    MembershipSummary,
    MeOut,
    RefreshIn,
    SessionOut,
    TokenOut,
    TokenPasswordIn,
    UserOut,
)
from app.core.errors import NotFound
from app.modules.access.catalog import PERMISSION_CODES
from app.modules.access.models import Membership, MembershipStatus
from app.modules.access.service import effective_permissions
from app.modules.auth import service as auth
from app.modules.auth.models import LoginEvent
from app.modules.businesses.models import Business, BusinessStatus

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens_out(t: auth.IssuedTokens) -> TokenOut:
    return TokenOut(access_token=t.access_token, expires_in=t.expires_in,
                    refresh_token=t.refresh_token)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: DB) -> TokenOut:
    tokens = auth.login(db, email=body.email, password=body.password)
    db.commit()
    return _tokens_out(tokens)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: DB) -> TokenOut:
    tokens = auth.refresh(db, body.refresh_token)
    db.commit()
    return _tokens_out(tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(p: CurrentPrincipal, db: DB) -> None:
    auth.logout(db, user=p.user, session_id=p.session_id)
    db.commit()


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: ChangePasswordIn, p: CurrentPrincipal, db: DB) -> None:
    auth.change_password(db, user=p.user, session_id=p.session_id,
                         current=body.current_password, new=body.new_password)
    db.commit()


@router.post("/accept-invite", status_code=status.HTTP_204_NO_CONTENT)
def accept_invite(body: TokenPasswordIn, db: DB) -> None:
    auth.accept_invite(db, token=body.token, password=body.password)
    db.commit()


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: TokenPasswordIn, db: DB) -> None:
    auth.reset_password(db, token=body.token, password=body.password)
    db.commit()


@router.get("/me", response_model=MeOut)
def me(p: CurrentPrincipal, db: DB) -> MeOut:
    """The signed-in user and, per accessible business, their role and effective permissions."""
    user = p.user
    summaries: list[MembershipSummary] = []
    if user.owner:
        for b in db.scalars(select(Business).order_by(Business.name)):
            summaries.append(MembershipSummary(
                business_id=b.id, business_name=b.name, role="owner",
                permissions=sorted(PERMISSION_CODES), all_branches=True, branch_ids=[]))
    else:
        rows = db.execute(
            select(Membership, Business)
            .join(Business, Business.id == Membership.business_id)
            .where(Membership.user_id == user.id,
                   Membership.status == MembershipStatus.ACTIVE,
                   Business.status == BusinessStatus.ACTIVE)
            .order_by(Business.name)
        ).all()
        for m, b in rows:
            summaries.append(MembershipSummary(
                business_id=b.id, business_name=b.name, role=m.role.code,
                permissions=sorted(effective_permissions(m)), all_branches=m.all_branches,
                branch_ids=sorted(m.branch_ids)))
    return MeOut(user=UserOut.of(user), businesses=summaries)


@router.get("/sessions", response_model=list[SessionOut])
def sessions(p: CurrentPrincipal, db: DB) -> list[SessionOut]:
    out = []
    for s in auth.list_sessions(db, p.user):
        item = SessionOut.model_validate(s)
        item.current = s.id == p.session_id
        out.append(item)
    return out


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(session_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> None:
    if not auth.revoke_session(db, user=p.user, session_id=session_id):
        raise NotFound("Session not found")
    db.commit()


@router.get("/login-history", response_model=Page[LoginEventOut])
def login_history(
    p: CurrentPrincipal, db: DB, params: Annotated[PageParams, Depends(page_params)]
) -> Page[LoginEventOut]:
    stmt = (select(LoginEvent).where(LoginEvent.user_id == p.user.id)
            .order_by(LoginEvent.created_at.desc()))
    rows, total = paginate(db, stmt, params)
    return Page(items=[LoginEventOut.model_validate(r) for r in rows], total=total,
                limit=params.limit, offset=params.offset)
