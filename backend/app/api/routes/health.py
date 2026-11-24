from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DB, OwnerPrincipal
from app.db.maintenance import check_integrity

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: DB) -> dict:
    """Liveness + database reachability. Unauthenticated; reveals nothing sensitive."""
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/system/db-integrity")
def db_integrity(_: OwnerPrincipal, db: DB) -> dict:
    report = check_integrity(db.get_bind())
    return {"ok": report.ok, "dialect": report.dialect, "problems": report.problems}
