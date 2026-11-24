"""Roles, business memberships and per-member permission overrides."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import str_enum
from app.db.base import Base, Timestamps, UUIDPrimaryKey


class Role(UUIDPrimaryKey, Timestamps, Base):
    """A named permission set inside one business.

    System roles are seeded into every business from ``catalog.SYSTEM_ROLES`` and cannot be
    edited or deleted; custom roles are created by business administrators.
    """

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("business_id", "code"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def permission_codes(self) -> set[str]:
        return {p.permission_code for p in self.permissions}


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(String(64), primary_key=True)

    role: Mapped[Role] = relationship(back_populates="permissions")


class MembershipStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Membership(UUIDPrimaryKey, Timestamps, Base):
    """Grants one user access to one business, with a role and branch scope."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "business_id"),
        Index("ix_memberships_business_status", "business_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        str_enum(MembershipStatus, 16),
        default=MembershipStatus.ACTIVE,
        nullable=False,
    )
    # When true the member may act in every branch, including ones created later.
    all_branches: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    role: Mapped[Role] = relationship(lazy="joined")
    branches: Mapped[list[MembershipBranch]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    overrides: Mapped[list[PermissionOverride]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def branch_ids(self) -> set[uuid.UUID]:
        return {b.branch_id for b in self.branches}


class MembershipBranch(Base):
    __tablename__ = "membership_branches"

    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True
    )


class OverrideEffect(enum.StrEnum):
    GRANT = "grant"
    DENY = "deny"


class PermissionOverride(Base):
    """Per-member exception to the role: explicitly grant or deny one permission."""

    __tablename__ = "permission_overrides"

    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    effect: Mapped[OverrideEffect] = mapped_column(
        str_enum(OverrideEffect, 8), nullable=False
    )
