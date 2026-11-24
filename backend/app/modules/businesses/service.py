"""Business lifecycle, branches and financial years."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.modules.access import service as access_service
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.businesses.models import (
    Branch,
    BranchKind,
    Business,
    BusinessStatus,
    FinancialYear,
)
from app.modules.events import service as events
from app.modules.users.models import User

BUSINESS_FIELDS = [
    "name", "legal_name", "gstin", "pan", "gst_registration_type", "phone", "email",
    "base_currency", "fy_start_month", "address_line1", "address_line2", "city", "state_code",
    "pincode", "country", "settings", "inventory_valuation",
]
BRANCH_FIELDS = [
    "code", "name", "kind", "gstin", "phone", "is_active", "address_line1", "address_line2",
    "city", "state_code", "pincode", "country",
]


# ---------------------------------------------------------------------------- financial years


def financial_year_for(day: date, fy_start_month: int) -> tuple[date, date, str]:
    """The financial year containing ``day``. India's default is April–March (month 4)."""
    start_year = day.year if day.month >= fy_start_month else day.year - 1
    start = date(start_year, fy_start_month, 1)
    end = date(start_year + 1, fy_start_month, 1) - timedelta(days=1)
    code = str(start_year) if fy_start_month == 1 else f"{start_year}-{str(start_year + 1)[-2:]}"
    return start, end, code


def list_financial_years(db: Session, business_id: uuid.UUID) -> list[FinancialYear]:
    return list(
        db.scalars(
            select(FinancialYear)
            .where(FinancialYear.business_id == business_id)
            .order_by(FinancialYear.start_date.desc())
        )
    )


def create_financial_year(
    db: Session, access: BusinessAccess, *, containing: date, actor: User | None = None
) -> FinancialYear:
    business = access.business
    start, end, code = financial_year_for(containing, business.fy_start_month)
    overlap = db.scalar(
        select(FinancialYear.id).where(
            FinancialYear.business_id == business.id,
            FinancialYear.start_date <= end,
            FinancialYear.end_date >= start,
        )
    )
    if overlap:
        raise Conflict(f"Financial year {code} overlaps an existing financial year")
    fy = FinancialYear(business_id=business.id, code=code, start_date=start, end_date=end)
    db.add(fy)
    db.flush()
    audit.record(db, action="financial_year.created", entity_type="financial_year",
                 entity_id=fy.id, business_id=business.id,
                 actor_id=(actor or access.user).id, changes={"code": code})
    return fy


# ---------------------------------------------------------------------------- businesses


def list_businesses(
    db: Session, user: User, *, include_archived: bool = False
) -> list[Business]:
    stmt = select(Business).order_by(Business.name)
    ids = access_service.accessible_business_ids(db, user)
    if ids is not None:
        stmt = stmt.where(Business.id.in_(ids))
    elif not include_archived:
        stmt = stmt.where(Business.status == BusinessStatus.ACTIVE)
    return list(db.scalars(stmt))


def create_business(db: Session, *, actor: User, data: dict[str, Any], today: date) -> Business:
    """Owner-only. Creates the business together with its head-office branch, the current
    financial year and the built-in roles, in one transaction."""
    if not actor.owner:
        raise PermissionDenied("Only the owner can create businesses")
    business = Business(**data)
    db.add(business)
    db.flush()

    head = Branch(
        business_id=business.id, code="HO", name="Head Office", kind=BranchKind.HEAD_OFFICE,
        gstin=business.gstin, address_line1=business.address_line1,
        address_line2=business.address_line2, city=business.city,
        state_code=business.state_code, pincode=business.pincode, country=business.country,
        phone=business.phone,
    )
    db.add(head)
    access_service.seed_system_roles(db, business.id)
    db.flush()
    from app.modules.catalog.service import seed_units
    from app.modules.inventory.service import create_default_warehouse

    seed_units(db, business.id)
    create_default_warehouse(db, business.id, head.id)
    db.flush()

    access = BusinessAccess(actor, business, None, frozenset(), True, frozenset())
    create_financial_year(db, access, containing=today)

    snap = audit.snapshot(business, BUSINESS_FIELDS)
    audit.record(db, action="business.created", entity_type="business", entity_id=business.id,
                 business_id=business.id, actor_id=actor.id, changes=snap)
    events.emit(db, "business.created", aggregate_type="business", aggregate_id=business.id,
                business_id=business.id, payload={"id": business.id, **snap})
    return business


def update_business(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Business:
    access.require("business.settings")
    business = access.business
    if business.status == BusinessStatus.ARCHIVED:
        raise ValidationFailed("Restore the business before editing it")
    if "fy_start_month" in data and data["fy_start_month"] != business.fy_start_month:
        if list_financial_years(db, business.id):
            raise ValidationFailed(
                "The financial-year start month cannot change once financial years exist"
            )
    if data.get("inventory_valuation", business.inventory_valuation) != business.inventory_valuation:
        from app.modules.inventory.service import has_stock_activity

        if has_stock_activity(db, business.id):
            raise ValidationFailed(
                "The inventory valuation method cannot change once stock has been recorded"
            )
    before = audit.snapshot(business, BUSINESS_FIELDS)
    for k, v in data.items():
        setattr(business, k, v)
    after = audit.snapshot(business, BUSINESS_FIELDS)
    changes = audit.diff(before, after)
    if changes:
        audit.record(db, action="business.updated", entity_type="business",
                     entity_id=business.id, business_id=business.id, actor_id=access.user.id,
                     changes=changes)
        events.emit(db, "business.updated", aggregate_type="business",
                    aggregate_id=business.id, business_id=business.id,
                    payload={"id": business.id, "changes": changes})
    return business


def set_business_status(db: Session, access: BusinessAccess, status: BusinessStatus) -> Business:
    if not access.is_owner:
        raise PermissionDenied("Only the owner can archive or restore businesses")
    business = access.business
    if business.status == status:
        return business
    business.status = status
    business.archived_at = datetime.now(UTC) if status == BusinessStatus.ARCHIVED else None
    action = "business.archived" if status == BusinessStatus.ARCHIVED else "business.restored"
    audit.record(db, action=action, entity_type="business", entity_id=business.id,
                 business_id=business.id, actor_id=access.user.id)
    events.emit(db, "business.updated", aggregate_type="business", aggregate_id=business.id,
                business_id=business.id, payload={"id": business.id, "status": status.value})
    return business


# ---------------------------------------------------------------------------- branches


def list_branches(db: Session, access: BusinessAccess, *, include_inactive: bool) -> list[Branch]:
    stmt = select(Branch).where(Branch.business_id == access.business.id).order_by(Branch.code)
    if not include_inactive:
        stmt = stmt.where(Branch.is_active.is_(True))
    if not access.all_branches:
        stmt = stmt.where(Branch.id.in_(access.branch_ids))
    return list(db.scalars(stmt))


def get_branch(db: Session, access: BusinessAccess, branch_id: uuid.UUID) -> Branch:
    branch = db.get(Branch, branch_id)
    if (
        branch is None
        or branch.business_id != access.business.id
        or not access.can_access_branch(branch.id)
    ):
        raise NotFound("Branch not found")
    return branch


def _ensure_code_free(db: Session, business_id: uuid.UUID, code: str, exclude: uuid.UUID | None) -> None:
    stmt = select(Branch.id).where(Branch.business_id == business_id, Branch.code == code)
    if exclude is not None:
        stmt = stmt.where(Branch.id != exclude)
    if db.scalar(stmt):
        raise Conflict(f"Branch code {code} is already in use")


def create_branch(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Branch:
    access.require("branches.manage")
    if not access.all_branches:
        raise PermissionDenied("Only members with access to all branches can create branches")
    _ensure_code_free(db, access.business.id, data["code"], None)
    if data.get("kind") == BranchKind.HEAD_OFFICE:
        raise ValidationFailed("A business has exactly one head office")
    branch = Branch(business_id=access.business.id, **data)
    db.add(branch)
    db.flush()
    audit.record(db, action="branch.created", entity_type="branch", entity_id=branch.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes=audit.snapshot(branch, BRANCH_FIELDS))
    return branch


def update_branch(db: Session, access: BusinessAccess, branch: Branch, data: dict[str, Any]) -> Branch:
    access.require("branches.manage")
    if "code" in data:
        _ensure_code_free(db, access.business.id, data["code"], branch.id)
    if branch.kind == BranchKind.HEAD_OFFICE and data.get("is_active") is False:
        raise ValidationFailed("The head office cannot be deactivated")
    if "kind" in data and data["kind"] != branch.kind:
        raise ValidationFailed("Branch kind cannot be changed")
    before = audit.snapshot(branch, BRANCH_FIELDS)
    for k, v in data.items():
        setattr(branch, k, v)
    audit.record(db, action="branch.updated", entity_type="branch", entity_id=branch.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes=audit.diff(before, audit.snapshot(branch, BRANCH_FIELDS)))
    return branch
