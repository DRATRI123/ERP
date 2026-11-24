"""Request/response schemas for the Phase 1 API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
)

from app.modules.access.models import MembershipStatus
from app.modules.businesses.models import (
    BranchKind,
    BusinessStatus,
    GstRegistrationType,
    InventoryValuation,
)
from app.modules.gst.gstin import validate_gstin, validate_pan, validate_state_code
from app.modules.users.models import UserStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9 \-]{6,20}$")]
GSTIN = Annotated[str, AfterValidator(validate_gstin)]
PAN = Annotated[str, AfterValidator(validate_pan)]
StateCode = Annotated[str, AfterValidator(validate_state_code)]
Password = Annotated[str, StringConstraints(min_length=1, max_length=128)]
BranchCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z0-9\-_]{1,20}$")
]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------- auth


class LoginIn(BaseModel):
    email: EmailStr
    password: Password


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str


class ChangePasswordIn(BaseModel):
    current_password: Password
    new_password: Password


class TokenPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: Password


class SessionOut(ORM):
    id: uuid.UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    current: bool = False


class LoginEventOut(ORM):
    id: uuid.UUID
    event: str
    success: bool
    failure_reason: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


# ---------------------------------------------------------------------------- users


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    is_owner: bool = Field(validation_alias="owner")
    status: UserStatus
    last_login_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, u: Any) -> UserOut:
        return cls.model_validate(u)


class UserUpdateIn(BaseModel):
    full_name: Name | None = None
    phone: Phone | None = None


class InviteOut(BaseModel):
    user: UserOut
    invite_token: str | None = Field(
        None, description="One-time token. Shown only once; deliver it to the user securely."
    )
    invite_expires_at: datetime | None = None


class ResetTokenOut(BaseModel):
    reset_token: str
    expires_at: datetime


class MembershipSummary(BaseModel):
    business_id: uuid.UUID
    business_name: str
    role: str | None
    permissions: list[str]
    all_branches: bool
    branch_ids: list[uuid.UUID]


class MeOut(BaseModel):
    user: UserOut
    businesses: list[MembershipSummary]


# ---------------------------------------------------------------------------- businesses


class AddressIn(BaseModel):
    address_line1: Annotated[str, StringConstraints(max_length=200)] | None = None
    address_line2: Annotated[str, StringConstraints(max_length=200)] | None = None
    city: Annotated[str, StringConstraints(max_length=100)] | None = None
    state_code: StateCode | None = None
    pincode: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")] | None = None
    country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] = "IN"


class BusinessCreateIn(AddressIn):
    name: Name
    legal_name: Name | None = None
    gstin: GSTIN | None = None
    pan: PAN | None = None
    gst_registration_type: GstRegistrationType = GstRegistrationType.REGULAR
    phone: Phone | None = None
    email: EmailStr | None = None
    base_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "INR"
    fy_start_month: int = Field(4, ge=1, le=12)
    inventory_valuation: InventoryValuation = InventoryValuation.WEIGHTED_AVERAGE
    settings: dict[str, Any] = Field(default_factory=dict)


class BusinessUpdateIn(BaseModel):
    name: Name | None = None
    legal_name: Name | None = None
    gstin: GSTIN | None = None
    pan: PAN | None = None
    gst_registration_type: GstRegistrationType | None = None
    phone: Phone | None = None
    email: EmailStr | None = None
    fy_start_month: int | None = Field(None, ge=1, le=12)
    address_line1: str | None = Field(None, max_length=200)
    address_line2: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    state_code: StateCode | None = None
    pincode: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")] | None = None
    inventory_valuation: InventoryValuation | None = None
    settings: dict[str, Any] | None = None


class BusinessOut(ORM):
    id: uuid.UUID
    name: str
    legal_name: str | None
    gstin: str | None
    pan: str | None
    gst_registration_type: GstRegistrationType
    phone: str | None
    email: str | None
    base_currency: str
    fy_start_month: int
    inventory_valuation: InventoryValuation
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state_code: str | None
    pincode: str | None
    country: str
    settings: dict[str, Any]
    status: BusinessStatus
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BranchCreateIn(AddressIn):
    code: BranchCode
    name: Name
    kind: BranchKind = BranchKind.BRANCH
    gstin: GSTIN | None = None
    phone: Phone | None = None


class BranchUpdateIn(BaseModel):
    code: BranchCode | None = None
    name: Name | None = None
    gstin: GSTIN | None = None
    phone: Phone | None = None
    is_active: bool | None = None
    address_line1: str | None = Field(None, max_length=200)
    address_line2: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    state_code: StateCode | None = None
    pincode: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")] | None = None


class BranchOut(ORM):
    id: uuid.UUID
    business_id: uuid.UUID
    code: str
    name: str
    kind: BranchKind
    gstin: str | None
    phone: str | None
    is_active: bool
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state_code: str | None
    pincode: str | None
    country: str
    created_at: datetime


class FinancialYearIn(BaseModel):
    containing_date: date = Field(description="Any date inside the financial year to create")


class FinancialYearOut(ORM):
    id: uuid.UUID
    code: str
    start_date: date
    end_date: date
    is_closed: bool


# ---------------------------------------------------------------------------- roles & members


class PermissionOut(BaseModel):
    code: str
    group: str
    description: str


class RoleIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    description: str | None = Field(None, max_length=500)
    permissions: set[str]


class RoleUpdateIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)] | None = None
    description: str | None = Field(None, max_length=500)
    permissions: set[str] | None = None


class RoleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]

    @classmethod
    def of(cls, r: Any) -> RoleOut:
        return cls(id=r.id, code=r.code, name=r.name, description=r.description,
                   is_system=r.is_system, permissions=sorted(r.permission_codes))


class MemberAddIn(BaseModel):
    email: EmailStr
    full_name: Name | None = Field(None, description="Required when the user does not exist yet")
    phone: Phone | None = None
    role_id: uuid.UUID
    all_branches: bool = True
    branch_ids: set[uuid.UUID] = Field(default_factory=set)
    grant_permissions: set[str] = Field(default_factory=set)
    deny_permissions: set[str] = Field(default_factory=set)


class MemberUpdateIn(BaseModel):
    role_id: uuid.UUID | None = None
    all_branches: bool | None = None
    branch_ids: set[uuid.UUID] | None = None
    grant_permissions: set[str] | None = None
    deny_permissions: set[str] | None = None


class MemberOut(BaseModel):
    id: uuid.UUID
    user: UserOut
    role: RoleOut
    status: MembershipStatus
    all_branches: bool
    branch_ids: list[uuid.UUID]
    grant_permissions: list[str]
    deny_permissions: list[str]
    effective_permissions: list[str]
    created_at: datetime


class MemberAddOut(BaseModel):
    member: MemberOut
    invite_token: str | None = Field(
        None, description="Present only when a new user was created. Shown once."
    )
    invite_expires_at: datetime | None = None


# ---------------------------------------------------------------------------- audit


class AuditLogOut(ORM):
    id: uuid.UUID
    business_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    changes: dict[str, Any] | None
    ip_address: str | None
    request_id: str | None
    created_at: datetime
