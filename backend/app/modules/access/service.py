"""Authorization: who may do what, in which business and branch.

Rules enforced here (and only here — routers call these, never re-implement them):

* The owner holds every permission in every business, including archived ones.
* Anyone else needs an *active* membership in an *active* business. Without one, the business
  is reported as not found so its existence is not disclosed.
* Effective permissions = role permissions ∪ granted overrides − denied overrides.
* Anti-escalation: a non-owner can only hand out permissions and branches they hold themselves,
  can only manage members whose access is a subset of their own, and cannot edit themselves.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.modules.access.catalog import PERMISSION_CODES, SYSTEM_ROLES
from app.modules.access.models import (
    Membership,
    MembershipBranch,
    MembershipStatus,
    OverrideEffect,
    PermissionOverride,
    Role,
    RolePermission,
)
from app.modules.audit import service as audit
from app.modules.businesses.models import Branch, Business, BusinessStatus
from app.modules.users.models import User, UserStatus


@dataclass(frozen=True)
class BusinessAccess:
    """The resolved access of one user to one business, for the current request."""

    user: User
    business: Business
    membership: Membership | None  # None for the owner
    permissions: frozenset[str]
    all_branches: bool
    branch_ids: frozenset[uuid.UUID]

    @property
    def is_owner(self) -> bool:
        return self.user.owner

    def can(self, code: str) -> bool:
        return code in self.permissions

    def require(self, *codes: str) -> None:
        missing = [c for c in codes if c not in self.permissions]
        if missing:
            raise PermissionDenied(
                "You do not have permission to perform this action",
                details={"missing_permissions": missing},
            )

    def can_access_branch(self, branch_id: uuid.UUID) -> bool:
        return self.all_branches or branch_id in self.branch_ids


# ---------------------------------------------------------------------------- resolution


def effective_permissions(membership: Membership) -> frozenset[str]:
    perms = set(membership.role.permission_codes)
    for o in membership.overrides:
        if o.effect == OverrideEffect.GRANT:
            perms.add(o.permission_code)
    for o in membership.overrides:
        if o.effect == OverrideEffect.DENY:
            perms.discard(o.permission_code)
    return frozenset(perms & PERMISSION_CODES)


def get_business_access(db: Session, user: User, business_id: uuid.UUID) -> BusinessAccess:
    business = db.get(Business, business_id)
    if business is None:
        raise NotFound("Business not found")
    if user.owner:
        return BusinessAccess(
            user, business, None, PERMISSION_CODES, all_branches=True, branch_ids=frozenset()
        )
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id, Membership.business_id == business_id
        )
    )
    if (
        membership is None
        or membership.status != MembershipStatus.ACTIVE
        or business.status != BusinessStatus.ACTIVE
    ):
        raise NotFound("Business not found")
    return BusinessAccess(
        user,
        business,
        membership,
        effective_permissions(membership),
        all_branches=membership.all_branches,
        branch_ids=frozenset(membership.branch_ids),
    )


def accessible_business_ids(db: Session, user: User) -> list[uuid.UUID] | None:
    """Business ids the user may open; ``None`` means all (owner)."""
    if user.owner:
        return None
    return list(
        db.scalars(
            select(Membership.business_id)
            .join(Business, Business.id == Membership.business_id)
            .where(
                Membership.user_id == user.id,
                Membership.status == MembershipStatus.ACTIVE,
                Business.status == BusinessStatus.ACTIVE,
            )
        )
    )


# ---------------------------------------------------------------------------- roles


def seed_system_roles(db: Session, business_id: uuid.UUID) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for tpl in SYSTEM_ROLES:
        role = Role(
            business_id=business_id,
            code=tpl.code,
            name=tpl.name,
            description=tpl.description,
            is_system=True,
        )
        db.add(role)
        roles[tpl.code] = role
    return roles


def _validate_codes(codes: set[str]) -> None:
    unknown = codes - PERMISSION_CODES
    if unknown:
        raise ValidationFailed("Unknown permission codes", details={"unknown": sorted(unknown)})


def _require_subset(access: BusinessAccess, codes: set[str] | frozenset[str]) -> None:
    if access.is_owner:
        return
    beyond = set(codes) - access.permissions
    if beyond:
        raise PermissionDenied(
            "You cannot grant permissions you do not hold yourself",
            details={"permissions": sorted(beyond)},
        )


def get_role(db: Session, business_id: uuid.UUID, role_id: uuid.UUID) -> Role:
    role = db.get(Role, role_id)
    if role is None or role.business_id != business_id:
        raise NotFound("Role not found")
    return role


def list_roles(db: Session, business_id: uuid.UUID) -> list[Role]:
    return list(
        db.scalars(
            select(Role)
            .where(Role.business_id == business_id)
            .order_by(Role.is_system.desc(), Role.name)
        )
    )


def _slug(name: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    return "custom_" + ("_".join(filter(None, out.split("_"))) or "role")


def create_role(
    db: Session, access: BusinessAccess, *, name: str, description: str | None, permissions: set[str]
) -> Role:
    _validate_codes(permissions)
    _require_subset(access, permissions)
    code = _slug(name)
    if db.scalar(select(Role.id).where(Role.business_id == access.business.id, Role.code == code)):
        raise Conflict("A role with this name already exists")
    role = Role(
        business_id=access.business.id,
        code=code,
        name=name,
        description=description,
        is_system=False,
        permissions=[RolePermission(permission_code=c) for c in sorted(permissions)],
    )
    db.add(role)
    db.flush()
    audit.record(
        db, action="role.created", entity_type="role", entity_id=role.id,
        business_id=access.business.id, actor_id=access.user.id,
        changes={"name": name, "permissions": sorted(permissions)},
    )
    return role


def update_role(
    db: Session,
    access: BusinessAccess,
    role: Role,
    *,
    name: str | None,
    description: str | None,
    permissions: set[str] | None,
) -> Role:
    if role.is_system:
        raise PermissionDenied("System roles cannot be modified; create a custom role instead")
    changes: dict = {}
    if permissions is not None:
        _validate_codes(permissions)
        # Changing a role affects everyone holding it, so the actor must hold both the old
        # and the new permission sets (no stripping or adding permissions beyond their own).
        _require_subset(access, permissions | role.permission_codes)
        changes["permissions"] = {
            "from": sorted(role.permission_codes), "to": sorted(permissions)
        }
        for rp in list(role.permissions):
            if rp.permission_code not in permissions:
                role.permissions.remove(rp)
        have = role.permission_codes
        role.permissions.extend(
            RolePermission(permission_code=c) for c in sorted(permissions - have)
        )
    if name is not None and name != role.name:
        changes["name"] = {"from": role.name, "to": name}
        role.name = name
    if description is not None:
        role.description = description
    audit.record(
        db, action="role.updated", entity_type="role", entity_id=role.id,
        business_id=access.business.id, actor_id=access.user.id, changes=changes,
    )
    return role


def delete_role(db: Session, access: BusinessAccess, role: Role) -> None:
    if role.is_system:
        raise PermissionDenied("System roles cannot be deleted")
    _require_subset(access, role.permission_codes)
    in_use = db.scalar(select(func.count()).where(Membership.role_id == role.id))
    if in_use:
        raise Conflict("Role is assigned to members; reassign them first")
    audit.record(
        db, action="role.deleted", entity_type="role", entity_id=role.id,
        business_id=access.business.id, actor_id=access.user.id, changes={"name": role.name},
    )
    db.delete(role)


# ---------------------------------------------------------------------------- members


def count_active_members(db: Session, business_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count()).where(
            Membership.business_id == business_id, Membership.status == MembershipStatus.ACTIVE
        )
    ) or 0


def _check_capacity(db: Session, business_id: uuid.UUID) -> None:
    limit = get_settings().max_users_per_business
    if count_active_members(db, business_id) >= limit:
        raise Conflict(f"This business already has the maximum of {limit} active users")


def _validate_branch_scope(
    db: Session, access: BusinessAccess, all_branches: bool, branch_ids: set[uuid.UUID]
) -> None:
    if all_branches:
        if not access.all_branches:
            raise PermissionDenied("You cannot grant access to all branches")
        return
    if not branch_ids:
        raise ValidationFailed("Select at least one branch, or grant access to all branches")
    found = set(
        db.scalars(
            select(Branch.id).where(
                Branch.business_id == access.business.id, Branch.id.in_(branch_ids)
            )
        )
    )
    if found != branch_ids:
        raise ValidationFailed("One or more branches do not belong to this business")
    if not access.all_branches and not branch_ids <= access.branch_ids:
        raise PermissionDenied("You can only assign branches you have access to")


def _normalise_overrides(grants: set[str], denies: set[str]) -> None:
    _validate_codes(grants | denies)
    if grants & denies:
        raise ValidationFailed(
            "A permission cannot be both granted and denied",
            details={"permissions": sorted(grants & denies)},
        )


def _apply_scope(
    m: Membership,
    role: Role,
    all_branches: bool,
    branch_ids: set[uuid.UUID],
    grants: set[str],
    denies: set[str],
) -> None:
    m.role_id = role.id
    m.role = role
    m.all_branches = all_branches
    # Sync collections in place (rather than replacing them) so unchanged rows are kept and
    # the unit of work never inserts a duplicate primary key before deleting the old row.
    wanted_branches = set() if all_branches else set(branch_ids)
    for mb in list(m.branches):
        if mb.branch_id not in wanted_branches:
            m.branches.remove(mb)
    have = {mb.branch_id for mb in m.branches}
    m.branches.extend(MembershipBranch(branch_id=b) for b in sorted(wanted_branches - have))

    wanted = {c: OverrideEffect.GRANT for c in grants} | {c: OverrideEffect.DENY for c in denies}
    for o in list(m.overrides):
        if o.permission_code not in wanted:
            m.overrides.remove(o)
        else:
            o.effect = wanted[o.permission_code]
    have_codes = {o.permission_code for o in m.overrides}
    m.overrides.extend(
        PermissionOverride(permission_code=c, effect=wanted[c])
        for c in sorted(set(wanted) - have_codes)
    )


def _membership_snapshot(m: Membership) -> dict:
    return {
        "role": m.role.code,
        "status": m.status.value,
        "all_branches": m.all_branches,
        "branch_ids": sorted(str(b) for b in m.branch_ids),
        "grants": sorted(o.permission_code for o in m.overrides if o.effect == OverrideEffect.GRANT),
        "denies": sorted(o.permission_code for o in m.overrides if o.effect == OverrideEffect.DENY),
    }


def _guard_target(access: BusinessAccess, target: Membership) -> None:
    if access.is_owner:
        return
    if target.user_id == access.user.id:
        raise PermissionDenied("You cannot change your own access")
    _require_subset(access, effective_permissions(target))
    if not access.all_branches and (target.all_branches or not target.branch_ids <= access.branch_ids):
        raise PermissionDenied("This member has access beyond your branches")


def add_member(
    db: Session,
    access: BusinessAccess,
    *,
    user: User,
    role: Role,
    all_branches: bool,
    branch_ids: set[uuid.UUID],
    grants: set[str],
    denies: set[str],
) -> Membership:
    if user.owner:
        raise ValidationFailed("The owner already has full access to every business")
    if user.status == UserStatus.SUSPENDED:
        raise ValidationFailed("This user account is suspended")
    existing = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id, Membership.business_id == access.business.id
        )
    )
    if existing is not None:
        raise Conflict("This user is already a member of the business")
    _normalise_overrides(grants, denies)
    _validate_branch_scope(db, access, all_branches, branch_ids)
    m = Membership(user_id=user.id, business_id=access.business.id, status=MembershipStatus.ACTIVE)
    _apply_scope(m, role, all_branches, branch_ids, grants, denies)
    _require_subset(access, effective_permissions(m))
    _check_capacity(db, access.business.id)
    db.add(m)
    db.flush()
    audit.record(
        db, action="member.added", entity_type="membership", entity_id=m.id,
        business_id=access.business.id, actor_id=access.user.id,
        changes={"user_id": str(user.id), **_membership_snapshot(m)},
    )
    return m


def get_membership(db: Session, business_id: uuid.UUID, membership_id: uuid.UUID) -> Membership:
    m = db.get(Membership, membership_id)
    if m is None or m.business_id != business_id:
        raise NotFound("Member not found")
    return m


def update_member(
    db: Session,
    access: BusinessAccess,
    m: Membership,
    *,
    role: Role | None,
    all_branches: bool | None,
    branch_ids: set[uuid.UUID] | None,
    grants: set[str] | None,
    denies: set[str] | None,
) -> Membership:
    _guard_target(access, m)
    before = _membership_snapshot(m)
    new_role = role or m.role
    new_all = m.all_branches if all_branches is None else all_branches
    new_branches = m.branch_ids if branch_ids is None else branch_ids
    cur_grants = {o.permission_code for o in m.overrides if o.effect == OverrideEffect.GRANT}
    cur_denies = {o.permission_code for o in m.overrides if o.effect == OverrideEffect.DENY}
    new_grants = cur_grants if grants is None else grants
    new_denies = cur_denies if denies is None else denies
    _normalise_overrides(new_grants, new_denies)
    _validate_branch_scope(db, access, new_all, set(new_branches))
    _apply_scope(m, new_role, new_all, set(new_branches), new_grants, new_denies)
    _require_subset(access, effective_permissions(m))
    audit.record(
        db, action="member.updated", entity_type="membership", entity_id=m.id,
        business_id=access.business.id, actor_id=access.user.id,
        changes=audit.diff(before, _membership_snapshot(m)),
    )
    return m


def set_member_status(
    db: Session, access: BusinessAccess, m: Membership, status: MembershipStatus
) -> Membership:
    _guard_target(access, m)
    if m.status == status:
        return m
    if status == MembershipStatus.ACTIVE:
        _check_capacity(db, access.business.id)
    old = m.status
    m.status = status
    audit.record(
        db, action=f"member.{'reactivated' if status == MembershipStatus.ACTIVE else 'suspended'}",
        entity_type="membership", entity_id=m.id, business_id=access.business.id,
        actor_id=access.user.id, changes={"status": {"from": old.value, "to": status.value}},
    )
    return m


def remove_member(db: Session, access: BusinessAccess, m: Membership) -> None:
    _guard_target(access, m)
    audit.record(
        db, action="member.removed", entity_type="membership", entity_id=m.id,
        business_id=access.business.id, actor_id=access.user.id,
        changes={"user_id": str(m.user_id), **_membership_snapshot(m)},
    )
    db.delete(m)
