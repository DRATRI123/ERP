from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DB, Access, AnyPrincipal, OwnerPrincipal, require
from app.api.schemas import (
    BranchCreateIn,
    BranchOut,
    BranchUpdateIn,
    BusinessCreateIn,
    BusinessOut,
    BusinessUpdateIn,
    FinancialYearIn,
    FinancialYearOut,
)
from app.core.clock import local_today
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.businesses import service
from app.modules.businesses.models import BusinessStatus
from app.modules.businesses.modules import BY_KEY, MODULES, enabled_modules

router = APIRouter(prefix="/businesses", tags=["businesses"])


@router.get("", response_model=list[BusinessOut])
def list_businesses(
    p: AnyPrincipal, db: DB, include_archived: bool = Query(False)
) -> list[BusinessOut]:
    """Businesses the caller may access. Only the owner can see archived businesses."""
    if p.is_api:
        from app.modules.businesses.models import BusinessStatus

        rows = [b for b in service.list_businesses(db, p.user, include_archived=False)
                if str(b.id) in p.api_client.business_ids and b.status == BusinessStatus.ACTIVE]
        return [BusinessOut.model_validate(b) for b in rows if "businesses:read" in p.api_scopes]
    rows = service.list_businesses(db, p.user, include_archived=include_archived)
    return [BusinessOut.model_validate(b) for b in rows]


@router.post("", response_model=BusinessOut, status_code=status.HTTP_201_CREATED)
def create_business(body: BusinessCreateIn, p: OwnerPrincipal, db: DB) -> BusinessOut:
    b = service.create_business(db, actor=p.user, data=body.model_dump(), today=local_today())
    db.commit()
    return BusinessOut.model_validate(b)


@router.get("/{business_id}", response_model=BusinessOut)
def get_business(access: Annotated[BusinessAccess, Depends(require("business.view"))]) -> BusinessOut:
    return BusinessOut.model_validate(access.business)


class ModulesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modules: dict[str, bool] = Field(description="Module key -> on/off; keys not sent keep their setting")


def _modules_out(settings: dict | None) -> dict[str, Any]:
    on = enabled_modules(settings)
    return {"modules": [{"key": m.key, "label": m.label, "description": m.description, "enabled": on[m.key],
                         "default": m.default} for m in MODULES], "enabled": on}


@router.get("/{business_id}/modules")
def get_modules(access: Annotated[BusinessAccess, Depends(require("business.view"))]) -> dict[str, Any]:
    """Which optional modules this business uses. The core (sales, stock, books, GST) is always on."""
    return _modules_out(access.business.settings)


@router.put("/{business_id}/modules")
def set_modules(body: ModulesIn, access: Annotated[BusinessAccess, Depends(require("business.settings"))],
                db: DB) -> dict[str, Any]:
    unknown = sorted(set(body.modules) - set(BY_KEY))
    if unknown:
        raise ValidationFailed(f"Unknown module: {', '.join(unknown)}")
    business = access.business
    before = enabled_modules(business.settings)
    stored = {**((business.settings or {}).get("modules") or {}), **body.modules}
    business.settings = {**(business.settings or {}), "modules": stored}
    after = enabled_modules(business.settings)
    changed = {k: {"before": before[k], "after": after[k]} for k in after if before[k] != after[k]}
    if changed:
        audit.record(db, action="business.modules_updated", entity_type="business", entity_id=business.id,
                     business_id=business.id, actor_id=access.user.id, changes=changed)
    db.commit()
    return _modules_out(business.settings)


@router.patch("/{business_id}", response_model=BusinessOut)
def update_business(body: BusinessUpdateIn, access: Access, db: DB) -> BusinessOut:
    b = service.update_business(db, access, body.model_dump(exclude_unset=True))
    db.commit()
    return BusinessOut.model_validate(b)


@router.post("/{business_id}/archive", response_model=BusinessOut)
def archive_business(access: Access, db: DB) -> BusinessOut:
    b = service.set_business_status(db, access, BusinessStatus.ARCHIVED)
    db.commit()
    return BusinessOut.model_validate(b)


@router.post("/{business_id}/restore", response_model=BusinessOut)
def restore_business(access: Access, db: DB) -> BusinessOut:
    b = service.set_business_status(db, access, BusinessStatus.ACTIVE)
    db.commit()
    return BusinessOut.model_validate(b)


# ---------------------------------------------------------------------------- branches


@router.get("/{business_id}/branches", response_model=list[BranchOut])
def list_branches(
    access: Annotated[BusinessAccess, Depends(require("business.view"))],
    db: DB,
    include_inactive: bool = Query(False),
) -> list[BranchOut]:
    rows = service.list_branches(db, access, include_inactive=include_inactive)
    return [BranchOut.model_validate(b) for b in rows]


@router.post(
    "/{business_id}/branches", response_model=BranchOut, status_code=status.HTTP_201_CREATED
)
def create_branch(body: BranchCreateIn,
                  access: Annotated[BusinessAccess, Depends(require("branches.manage"))], db: DB) -> BranchOut:
    b = service.create_branch(db, access, body.model_dump())
    db.commit()
    return BranchOut.model_validate(b)


@router.get("/{business_id}/branches/{branch_id}", response_model=BranchOut)
def get_branch(
    branch_id: uuid.UUID,
    access: Annotated[BusinessAccess, Depends(require("business.view"))],
    db: DB,
) -> BranchOut:
    return BranchOut.model_validate(service.get_branch(db, access, branch_id))


@router.patch("/{business_id}/branches/{branch_id}", response_model=BranchOut)
def update_branch(branch_id: uuid.UUID, body: BranchUpdateIn, access: Access, db: DB) -> BranchOut:
    branch = service.get_branch(db, access, branch_id)
    b = service.update_branch(db, access, branch, body.model_dump(exclude_unset=True))
    db.commit()
    return BranchOut.model_validate(b)


# ---------------------------------------------------------------------------- financial years


@router.get("/{business_id}/financial-years", response_model=list[FinancialYearOut])
def list_financial_years(
    access: Annotated[BusinessAccess, Depends(require("business.view"))], db: DB
) -> list[FinancialYearOut]:
    return [FinancialYearOut.model_validate(f)
            for f in service.list_financial_years(db, access.business.id)]


@router.post(
    "/{business_id}/financial-years",
    response_model=FinancialYearOut,
    status_code=status.HTTP_201_CREATED,
)
def create_financial_year(
    body: FinancialYearIn,
    access: Annotated[BusinessAccess, Depends(require("financial_years.manage"))],
    db: DB,
) -> FinancialYearOut:
    fy = service.create_financial_year(db, access, containing=body.containing_date)
    db.commit()
    return FinancialYearOut.model_validate(fy)
