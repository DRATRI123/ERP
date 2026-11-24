"""Permission catalog, roles and business members."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentPrincipal, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import (
    InviteOut,
    MemberAddIn,
    MemberAddOut,
    MemberOut,
    MemberUpdateIn,
    PermissionOut,
    RoleIn,
    RoleOut,
    RoleUpdateIn,
    UserOut,
)
from app.core.errors import ValidationFailed
from app.modules.access import service
from app.modules.access.catalog import PERMISSIONS
from app.modules.access.models import Membership, MembershipStatus, OverrideEffect
from app.modules.access.service import BusinessAccess
from app.modules.auth import service as auth
from app.modules.auth.models import TokenPurpose
from app.modules.users import service as users
from app.modules.users.models import User, UserStatus

router = APIRouter(tags=["access"])

ViewUsers = Annotated[BusinessAccess, Depends(require("users.view"))]
ManageUsers = Annotated[BusinessAccess, Depends(require("users.manage"))]
ManageRoles = Annotated[BusinessAccess, Depends(require("roles.manage"))]


@router.get("/permissions", response_model=list[PermissionOut])
def permission_catalog(_: CurrentPrincipal) -> list[PermissionOut]:
    return [PermissionOut(code=p.code, group=p.group, description=p.description) for p in PERMISSIONS]


# ---------------------------------------------------------------------------- roles


@router.get("/businesses/{business_id}/roles", response_model=list[RoleOut])
def list_roles(access: ViewUsers, db: DB) -> list[RoleOut]:
    return [RoleOut.of(r) for r in service.list_roles(db, access.business.id)]


@router.post("/businesses/{business_id}/roles", response_model=RoleOut,
             status_code=status.HTTP_201_CREATED)
def create_role(body: RoleIn, access: ManageRoles, db: DB) -> RoleOut:
    role = service.create_role(db, access, name=body.name, description=body.description,
                               permissions=body.permissions)
    db.commit()
    return RoleOut.of(role)


@router.patch("/businesses/{business_id}/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: uuid.UUID, body: RoleUpdateIn, access: ManageRoles, db: DB) -> RoleOut:
    role = service.get_role(db, access.business.id, role_id)
    role = service.update_role(db, access, role, name=body.name, description=body.description,
                               permissions=body.permissions)
    db.commit()
    return RoleOut.of(role)


@router.delete("/businesses/{business_id}/roles/{role_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_role(role_id: uuid.UUID, access: ManageRoles, db: DB) -> None:
    service.delete_role(db, access, service.get_role(db, access.business.id, role_id))
    db.commit()


# ---------------------------------------------------------------------------- members


def _member_out(db: Session, m: Membership) -> MemberOut:
    user = db.get(User, m.user_id)
    return MemberOut(
        id=m.id,
        user=UserOut.of(user),
        role=RoleOut.of(m.role),
        status=m.status,
        all_branches=m.all_branches,
        branch_ids=sorted(m.branch_ids),
        grant_permissions=sorted(o.permission_code for o in m.overrides
                                 if o.effect == OverrideEffect.GRANT),
        deny_permissions=sorted(o.permission_code for o in m.overrides
                                if o.effect == OverrideEffect.DENY),
        effective_permissions=sorted(service.effective_permissions(m)),
        created_at=m.created_at,
    )


@router.get("/businesses/{business_id}/members", response_model=Page[MemberOut])
def list_members(
    access: ViewUsers, db: DB, params: Annotated[PageParams, Depends(page_params)],
    status_filter: MembershipStatus | None = None,
) -> Page[MemberOut]:
    stmt = (select(Membership).join(User, User.id == Membership.user_id)
            .where(Membership.business_id == access.business.id).order_by(User.full_name))
    if status_filter is not None:
        stmt = stmt.where(Membership.status == status_filter)
    rows, total = paginate(db, stmt, params)
    return Page(items=[_member_out(db, m) for m in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.post("/businesses/{business_id}/members", response_model=MemberAddOut,
             status_code=status.HTTP_201_CREATED)
def add_member(body: MemberAddIn, access: ManageUsers, db: DB) -> MemberAddOut:
    """Add an existing user to this business, or invite a new user by email."""
    role = service.get_role(db, access.business.id, body.role_id)
    user = users.get_by_email(db, body.email)
    invite_token = invite_expires = None
    if user is None:
        if not body.full_name:
            raise ValidationFailed("full_name is required to invite a new user")
        user = users.create_invited_user(db, email=body.email, full_name=body.full_name,
                                         phone=body.phone, created_by=access.user)
    m = service.add_member(
        db, access, user=user, role=role, all_branches=body.all_branches,
        branch_ids=body.branch_ids, grants=body.grant_permissions, denies=body.deny_permissions,
    )
    if user.status == UserStatus.INVITED:
        invite_token, invite_expires = auth.issue_token(
            db, user=user, purpose=TokenPurpose.INVITE, created_by=access.user)
    db.commit()
    return MemberAddOut(member=_member_out(db, m), invite_token=invite_token,
                        invite_expires_at=invite_expires)


@router.get("/businesses/{business_id}/members/{membership_id}", response_model=MemberOut)
def get_member(membership_id: uuid.UUID, access: ViewUsers, db: DB) -> MemberOut:
    return _member_out(db, service.get_membership(db, access.business.id, membership_id))


@router.patch("/businesses/{business_id}/members/{membership_id}", response_model=MemberOut)
def update_member(
    membership_id: uuid.UUID, body: MemberUpdateIn, access: ManageUsers, db: DB
) -> MemberOut:
    m = service.get_membership(db, access.business.id, membership_id)
    role = service.get_role(db, access.business.id, body.role_id) if body.role_id else None
    m = service.update_member(
        db, access, m, role=role, all_branches=body.all_branches, branch_ids=body.branch_ids,
        grants=body.grant_permissions, denies=body.deny_permissions,
    )
    db.commit()
    return _member_out(db, m)


@router.post("/businesses/{business_id}/members/{membership_id}/suspend", response_model=MemberOut)
def suspend_member(membership_id: uuid.UUID, access: ManageUsers, db: DB) -> MemberOut:
    m = service.get_membership(db, access.business.id, membership_id)
    service.set_member_status(db, access, m, MembershipStatus.SUSPENDED)
    db.commit()
    return _member_out(db, m)


@router.post("/businesses/{business_id}/members/{membership_id}/reactivate",
             response_model=MemberOut)
def reactivate_member(membership_id: uuid.UUID, access: ManageUsers, db: DB) -> MemberOut:
    m = service.get_membership(db, access.business.id, membership_id)
    service.set_member_status(db, access, m, MembershipStatus.ACTIVE)
    db.commit()
    return _member_out(db, m)


@router.delete("/businesses/{business_id}/members/{membership_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def remove_member(membership_id: uuid.UUID, access: ManageUsers, db: DB) -> None:
    service.remove_member(db, access, service.get_membership(db, access.business.id, membership_id))
    db.commit()


@router.post("/businesses/{business_id}/members/{membership_id}/invite", response_model=InviteOut)
def reissue_invite(membership_id: uuid.UUID, access: ManageUsers, db: DB) -> InviteOut:
    """Issue a fresh invitation token for a member who has not accepted yet."""
    m = service.get_membership(db, access.business.id, membership_id)
    user = db.get(User, m.user_id)
    if user.status != UserStatus.INVITED:
        raise ValidationFailed("This user has already accepted their invitation")
    token, expires = auth.issue_token(db, user=user, purpose=TokenPurpose.INVITE,
                                      created_by=access.user)
    db.commit()
    return InviteOut(user=UserOut.of(user), invite_token=token, invite_expires_at=expires)
