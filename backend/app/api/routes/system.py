"""Owner-only operations: custom domains, backups, system health; plus probe endpoints."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DB, CurrentPrincipal, OwnerPrincipal
from app.api.pagination import Page, PageParams, page_params, paginate
from app.core import metrics
from app.core.config import get_settings
from app.core.errors import NotFound, PermissionDenied
from app.modules.system import backups, domains, maintenance, monitor, security
from app.modules.system.models import (
    BackupRun,
    BackupStatus,
    CustomDomain,
    DomainStatus,
)
from app.modules.users.models import User

router = APIRouter(tags=["system"])


# ---------------------------------------------------------------------------- probes


@router.get("/ready")
def ready(db: DB, response: Response) -> dict:
    """Readiness: database reachable, schema at the latest migration, Redis (if configured)
    and free disk. 503 when any check fails. Unauthenticated; reveals only pass/fail."""
    out = monitor.readiness(db)
    if not out["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return out


def _token_ok(expected: str | None, given: str | None) -> bool:
    return bool(expected) and bool(given) and secrets.compare_digest(expected, given)  # type: ignore[arg-type]


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def prometheus(db: DB, authorization: Annotated[str | None, Header()] = None) -> PlainTextResponse:
    expected = get_settings().metrics_token
    if not expected:
        raise NotFound("Not found")
    given = authorization.removeprefix("Bearer ").strip() if authorization else None
    if not _token_ok(expected, given):
        raise PermissionDenied("Invalid metrics token")
    return PlainTextResponse(metrics.render(monitor.gauges(db)),
                             media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/system/tls-ask", include_in_schema=False)
def tls_ask(db: DB, domain: str = Query(max_length=253), token: str = Query("", max_length=200)) -> Response:
    """Caddy on-demand TLS asks here before obtaining a certificate. 200 = allowed."""
    if not _token_ok(get_settings().tls_ask_token, token):
        raise PermissionDenied("Invalid token")
    if domains.certificate_allowed(db, domain):
        return Response(status_code=200)
    raise NotFound("Unknown domain")


@router.get("/system/public-address")
def public_address(_: CurrentPrincipal) -> dict:
    """Where phones outside this computer can reach the ERP: a server domain, a running
    online.bat tunnel, or none (links then only work on this computer / shop network)."""
    from app.modules.system.public_address import public_address as find

    return find()


@router.get("/system/health")
def system_health(_: OwnerPrincipal, db: DB) -> dict:
    return monitor.system_health(db)


# ---------------------------------------------------------------------------- domains


class DomainIn(BaseModel):
    hostname: str = Field(min_length=3, max_length=253)
    default_business_id: uuid.UUID | None = None


class DomainPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_business_id: uuid.UUID | None = None
    enabled: bool | None = None


class DomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    hostname: str
    status: DomainStatus
    default_business_id: uuid.UUID | None
    verified_at: datetime | None
    last_checked_at: datetime | None
    consecutive_failures: int
    last_error: str | None
    created_at: datetime
    dns_record: dict[str, str]

    @classmethod
    def of(cls, d: CustomDomain) -> DomainOut:
        return cls.model_validate({**{k: getattr(d, k) for k in (
            "id", "hostname", "status", "default_business_id", "verified_at", "last_checked_at",
            "consecutive_failures", "last_error", "created_at")}, "dns_record": domains.dns_instructions(d)})


@router.get("/system/domains", response_model=list[DomainOut])
def list_domains(_: OwnerPrincipal, db: DB) -> list[DomainOut]:
    return [DomainOut.of(d) for d in domains.list_domains(db)]


@router.post("/system/domains", response_model=DomainOut, status_code=status.HTTP_201_CREATED)
def add_domain(body: DomainIn, p: OwnerPrincipal, db: DB) -> DomainOut:
    """Add a domain. Then publish the returned TXT record and call ``/verify``; also point the
    domain's A/AAAA (or CNAME) record at this server."""
    d = domains.add_domain(db, hostname=body.hostname, default_business_id=body.default_business_id,
                           actor_id=p.user.id)
    db.commit()
    return DomainOut.of(d)


@router.post("/system/domains/{domain_id}/verify", response_model=DomainOut)
def verify_domain(domain_id: uuid.UUID, p: OwnerPrincipal, db: DB) -> DomainOut:
    d = domains.get_domain(db, domain_id)
    domains.check(db, d, actor_id=p.user.id)
    db.commit()
    return DomainOut.of(d)


@router.patch("/system/domains/{domain_id}", response_model=DomainOut)
def update_domain(domain_id: uuid.UUID, body: DomainPatch, p: OwnerPrincipal, db: DB) -> DomainOut:
    d = domains.get_domain(db, domain_id)
    kwargs: dict[str, Any] = {"enabled": body.enabled}
    if "default_business_id" in body.model_fields_set:
        kwargs["default_business_id"] = body.default_business_id
    domains.update_domain(db, d, actor_id=p.user.id, **kwargs)
    db.commit()
    return DomainOut.of(d)


@router.delete("/system/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_domain(domain_id: uuid.UUID, p: OwnerPrincipal, db: DB) -> None:
    domains.delete_domain(db, domains.get_domain(db, domain_id), actor_id=p.user.id)
    db.commit()


@router.get("/domain-info", tags=["system"])
def domain_info(db: DB, host: Annotated[str | None, Header()] = None) -> dict:
    """Public: which business the frontend should open on this host (if the owner chose one).
    Returns no names, only an id; authorization still applies to everything else."""
    name = (host or "").split(":")[0].lower().rstrip(".")
    from sqlalchemy import select

    d = db.scalar(select(CustomDomain).where(CustomDomain.hostname == name,
                                             CustomDomain.status == DomainStatus.ACTIVE))
    return {"custom_domain": d is not None, "default_business_id": d.default_business_id if d else None}


# ---------------------------------------------------------------------------- backups


class BackupSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    hour_utc: int | None = Field(None, ge=0, le=23)
    keep_daily: int | None = Field(None, ge=1, le=400)
    keep_weekly: int | None = Field(None, ge=0, le=400)
    keep_monthly: int | None = Field(None, ge=0, le=400)
    offsite_integration_id: uuid.UUID | None = None


class BackupRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    status: BackupStatus
    trigger: str
    filename: str | None
    size_bytes: int | None
    sha256: str | None
    verified: bool
    offsite: str | None
    error: str | None
    deleted_at: datetime | None


@router.get("/system/backups/settings")
def get_backup_settings(_: OwnerPrincipal, db: DB) -> dict:
    return {**backups.get_backup_settings(db), "key_configured": bool(get_settings().backup_key),
            "directory": get_settings().backup_dir}


@router.put("/system/backups/settings")
def put_backup_settings(body: BackupSettingsIn, p: OwnerPrincipal, db: DB) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if "offsite_integration_id" in changes and changes["offsite_integration_id"] is not None:
        changes["offsite_integration_id"] = str(changes["offsite_integration_id"])
    out = backups.save_backup_settings(db, changes)
    from app.modules.audit import service as audit

    audit.record(db, action="backup.settings_changed", entity_type="system", actor_id=p.user.id,
                 changes=changes)
    db.commit()
    return out


@router.get("/system/backups", response_model=Page[BackupRunOut])
def list_backups(_: OwnerPrincipal, db: DB,
                 params: Annotated[PageParams, Depends(page_params)]) -> Page[BackupRunOut]:
    from sqlalchemy import select

    rows, total = paginate(db, select(BackupRun).order_by(BackupRun.started_at.desc()), params)
    return Page(items=[BackupRunOut.model_validate(r) for r in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.post("/system/backups", response_model=BackupRunOut, status_code=status.HTTP_202_ACCEPTED)
def request_backup(p: OwnerPrincipal, db: DB) -> BackupRunOut:
    """Queue a backup now; the worker runs it within one loop (≈15 s)."""
    run = backups.queue_backup(db)
    from app.modules.audit import service as audit

    audit.record(db, action="backup.requested", entity_type="backup_run", entity_id=run.id, actor_id=p.user.id)
    db.commit()
    return BackupRunOut.model_validate(run)


@router.get("/system/backups/{run_id}/download", response_class=FileResponse)
def download_backup(run_id: uuid.UUID, p: OwnerPrincipal, db: DB) -> FileResponse:
    """The encrypted backup file (useless without ERP_BACKUP_KEY)."""
    run, path = backups.backup_path(db, run_id)
    from app.modules.audit import service as audit

    audit.record(db, action="backup.downloaded", entity_type="backup_run", entity_id=run.id, actor_id=p.user.id)
    db.commit()
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


# ---------------------------------------------------------------------------- users' 2FA


@router.delete("/system/users/{user_id}/2fa", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_mfa(user_id: uuid.UUID, p: OwnerPrincipal, db: DB) -> None:
    """Switch off 2FA for a member who lost their authenticator and recovery codes."""
    target = db.get(User, user_id)
    if target is None or target.owner:
        raise NotFound("User not found")
    security.owner_reset_mfa(db, target=target, actor=p.user)
    db.commit()


# ---------------------------------------------------------------------------- maintenance


class MaintenanceIn(BaseModel):
    enabled: bool
    message: str | None = Field(None, max_length=300)


@router.get("/system/maintenance")
def get_maintenance(_: OwnerPrincipal, db: DB) -> dict:
    return maintenance.get(db)


@router.put("/system/maintenance")
def put_maintenance(body: MaintenanceIn, p: OwnerPrincipal, db: DB) -> dict:
    """Pause all changes (reads keep working) — e.g. during the PostgreSQL cutover."""
    out = maintenance.set_mode(db, body.enabled, body.message, by=p.user.email)
    from app.modules.audit import service as audit

    audit.record(db, action=f"system.maintenance_{'on' if body.enabled else 'off'}",
                 entity_type="system", actor_id=p.user.id)
    db.commit()
    return out
