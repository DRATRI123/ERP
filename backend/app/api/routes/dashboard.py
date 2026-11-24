from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, require
from app.core.errors import NotFound
from app.modules.access.models import Membership, MembershipStatus
from app.modules.access.service import BusinessAccess, get_business_access
from app.modules.businesses.models import Business, BusinessStatus
from app.modules.dashboard import service

router = APIRouter(tags=["dashboard"])


@router.get("/businesses/{business_id}/dashboard")
def business_dashboard(access: Annotated[BusinessAccess, Depends(require("business.view"))], db: DB,
                       days: int = Query(30, ge=7, le=92)) -> dict:
    """Today's and this month's figures, outstanding balances, a daily sales series, recent
    documents and stock alerts. Sections the member may not see are omitted."""
    return service.business_dashboard(db, access, days=days)


@router.get("/dashboard/overview")
def overview(p: CurrentPrincipal, db: DB) -> list[dict]:
    """Business-wise performance this month across every business the user can open."""
    if p.user.owner:
        ids = db.scalars(select(Business.id).where(Business.status == BusinessStatus.ACTIVE)
                         .order_by(Business.name)).all()
    else:
        ids = db.scalars(select(Membership.business_id).join(Business, Business.id == Membership.business_id)
                         .where(Membership.user_id == p.user.id, Membership.status == MembershipStatus.ACTIVE,
                                Business.status == BusinessStatus.ACTIVE).order_by(Business.name)).all()
    accesses = []
    for bid in ids:
        try:
            accesses.append(get_business_access(db, p.user, bid))
        except NotFound:
            continue
    return service.overview(db, accesses)
