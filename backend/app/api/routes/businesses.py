from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, Access, CurrentPrincipal, OwnerPrincipal, require
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
from app.modules.access.service import BusinessAccess
from app.modules.businesses import service
from app.modules.businesses.models import BusinessStatus

router = APIRouter(prefix="/businesses", tags=["businesses"])


@router.get("", response_model=list[BusinessOut])
def list_businesses(
    p: CurrentPrincipal, db: DB, include_archived: bool = Query(False)
) -> list[BusinessOut]:
    """Businesses the caller may access. Only the owner can see archived businesses."""
    rows = service.list_businesses(db, p.user, include_archived=include_archived)
    return [BusinessOut.model_validate(b) for b in rows]


@router.post("", response_model=BusinessOut, status_code=status.HTTP_201_CREATED)
def create_business(body: BusinessCreateIn, p: OwnerPrincipal, db: DB) -> BusinessOut:
    b = service.create_business(db, actor=p.user, data=body.model_dump(), today=date.today())
    db.commit()
    return BusinessOut.model_validate(b)


@router.get("/{business_id}", response_model=BusinessOut)
def get_business(access: Annotated[BusinessAccess, Depends(require("business.view"))]) -> BusinessOut:
    return BusinessOut.model_validate(access.business)


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
def create_branch(body: BranchCreateIn, access: Access, db: DB) -> BranchOut:
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
