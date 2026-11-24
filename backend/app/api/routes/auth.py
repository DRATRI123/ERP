from __future__ import annotations

import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import (
    ChangePasswordIn,
    LoginEventOut,
    LoginIn,
    LoginOut,
    MembershipSummary,
    MeOut,
    MfaCodeIn,
    MfaConfirmIn,
    MfaLoginIn,
    MfaSetupOut,
    MfaStatusOut,
    PasswordConfirmIn,
    RecoveryCodesOut,
    RefreshIn,
    SecurityEventOut,
    SessionOut,
    TokenOut,
    TokenPasswordIn,
    UserOut,
)
from app.core.config import get_settings
from app.core.context import client_ip_var
from app.core.errors import (
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    TooManyRequests,
)
from app.core.ratelimit import limiter
from app.modules.access.catalog import PERMISSION_CODES
from app.modules.access.models import Membership, MembershipStatus
from app.modules.access.service import effective_permissions
from app.modules.auth import service as auth
from app.modules.auth.models import LoginEvent
from app.modules.businesses.models import Business, BusinessStatus
from app.modules.system import security
from app.modules.system.models import SecurityEvent

router = APIRouter(prefix="/auth", tags=["auth"])


REFRESH_COOKIE = "erp_refresh"
CSRF_COOKIE = "erp_csrf"
COOKIE_PATH = "/api/v1/auth"


def _ip_guard(request: Request, bucket: str = "login") -> None:
    """Per-IP limit on credential-guessing endpoints (accounts also lock individually)."""
    ip = client_ip_var.get() or "unknown"
    allowed, _, retry = limiter.hit(f"{bucket}-ip:{ip}", get_settings().login_ip_limit, window=900.0)
    if not allowed:
        raise TooManyRequests("Too many attempts from this network. Try again later.",
                              code="ip_rate_limited", headers={"Retry-After": str(retry)})


def _tokens_out(t: auth.IssuedTokens, request: Request, response: Response) -> TokenOut:
    if not get_settings().auth_cookie:
        return TokenOut(access_token=t.access_token, expires_in=t.expires_in, refresh_token=t.refresh_token)
    secure = request.url.scheme == "https" or get_settings().force_https
    max_age = get_settings().refresh_token_ttl_days * 86400
    response.set_cookie(REFRESH_COOKIE, t.refresh_token, max_age=max_age, path=COOKIE_PATH,
                        httponly=True, secure=secure, samesite="strict")
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(24), max_age=max_age, path="/",
                        httponly=False, secure=secure, samesite="strict")
    return TokenOut(access_token=t.access_token, expires_in=t.expires_in)


@router.post("/login", response_model=LoginOut, response_model_exclude_none=True)
def login(body: LoginIn, request: Request, response: Response, db: DB) -> LoginOut:
    _ip_guard(request)
    result = auth.login(db, email=body.email, password=body.password)
    db.commit()
    if isinstance(result, auth.MfaChallenge):
        return LoginOut(mfa_required=True, mfa_token=result.mfa_token, mfa_expires_in=result.expires_in)
    return LoginOut(**_tokens_out(result, request, response).model_dump())


@router.post("/login/mfa", response_model=TokenOut, response_model_exclude_none=True)
def login_mfa(body: MfaLoginIn, request: Request, response: Response, db: DB) -> TokenOut:
    """Second step of sign-in: the code from the authenticator app, or a recovery code."""
    _ip_guard(request)
    tokens = auth.login_mfa(db, mfa_token=body.mfa_token, code=body.code, recovery_code=body.recovery_code)
    db.commit()
    return _tokens_out(tokens, request, response)


@router.post("/refresh", response_model=TokenOut, response_model_exclude_none=True)
def refresh(body: RefreshIn, request: Request, response: Response, db: DB) -> TokenOut:
    token = body.refresh_token
    if token is None:
        token = request.cookies.get(REFRESH_COOKIE)
        if not token or not get_settings().auth_cookie:
            raise NotAuthenticated("Refresh token missing")
        # Double-submit check: a cross-site page can make the browser send the cookie but cannot
        # read the CSRF cookie to copy it into the header.
        csrf = request.cookies.get(CSRF_COOKIE) or ""
        if not csrf or not secrets.compare_digest(csrf, request.headers.get("x-csrf-token", "")):
            raise PermissionDenied("CSRF token missing or wrong", code="csrf_failed")
    tokens = auth.refresh(db, token)
    db.commit()
    return _tokens_out(tokens, request, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(p: CurrentPrincipal, response: Response, db: DB) -> None:
    auth.logout(db, user=p.user, session_id=p.session_id)
    db.commit()
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)
    response.delete_cookie(CSRF_COOKIE, path="/")


# ---------------------------------------------------------------------------- two-factor auth


@router.get("/2fa", response_model=MfaStatusOut)
def mfa_status(p: CurrentPrincipal, db: DB) -> MfaStatusOut:
    return MfaStatusOut(enabled=security.mfa_enabled(p.user), enabled_at=p.user.totp_enabled_at,
                        recovery_codes_left=security.recovery_codes_left(db, p.user))


@router.post("/2fa/setup", response_model=MfaSetupOut)
def mfa_setup(body: PasswordConfirmIn, p: CurrentPrincipal, db: DB) -> MfaSetupOut:
    """Step 1: get a secret / QR code for the authenticator app. Nothing changes until step 2."""
    out = security.begin_setup(db, p.user, body.password)
    db.commit()
    return MfaSetupOut(**out)


@router.post("/2fa/enable", response_model=RecoveryCodesOut)
def mfa_enable(body: MfaCodeIn, p: CurrentPrincipal, db: DB) -> RecoveryCodesOut:
    """Step 2: confirm with a code from the app. Returns recovery codes (shown once) and signs
    out every other session."""
    codes = security.enable(db, p.user, body.code, session_id=p.session_id)
    db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
def mfa_disable(body: MfaConfirmIn, p: CurrentPrincipal, db: DB) -> None:
    security.disable(db, p.user, password=body.password, code=body.code)
    db.commit()


@router.post("/2fa/recovery-codes", response_model=RecoveryCodesOut)
def mfa_recovery_codes(body: MfaConfirmIn, p: CurrentPrincipal, db: DB) -> RecoveryCodesOut:
    codes = security.regenerate_codes(db, p.user, password=body.password, code=body.code)
    db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.get("/security-events", response_model=Page[SecurityEventOut])
def security_events(
    p: CurrentPrincipal, db: DB, params: Annotated[PageParams, Depends(page_params)]
) -> Page[SecurityEventOut]:
    stmt = (select(SecurityEvent).where(SecurityEvent.user_id == p.user.id)
            .order_by(SecurityEvent.created_at.desc()))
    rows, total = paginate(db, stmt, params)
    return Page(items=[SecurityEventOut.model_validate(r) for r in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: ChangePasswordIn, p: CurrentPrincipal, db: DB) -> None:
    auth.change_password(db, user=p.user, session_id=p.session_id,
                         current=body.current_password, new=body.new_password)
    db.commit()


@router.post("/accept-invite", status_code=status.HTTP_204_NO_CONTENT)
def accept_invite(body: TokenPasswordIn, request: Request, db: DB) -> None:
    _ip_guard(request, "token")
    auth.accept_invite(db, token=body.token, password=body.password)
    db.commit()


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: TokenPasswordIn, request: Request, db: DB) -> None:
    _ip_guard(request, "token")
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
