"""Readiness, system health and scrape-time metrics."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, get_settings
from app.modules.system.models import (
    BackupRun,
    BackupStatus,
    CustomDomain,
    SystemSetting,
)

HEARTBEAT_PREFIX = "heartbeat:"
WORKER_STALE = timedelta(minutes=5)
BACKUP_STALE = timedelta(hours=26)


def heartbeat(db: Session, name: str, stats: dict[str, Any] | None = None) -> None:
    key = HEARTBEAT_PREFIX + name
    value = {"at": datetime.now(UTC).isoformat(), "stats": stats or {}}
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def last_heartbeat(db: Session, name: str = "worker") -> datetime | None:
    row = db.get(SystemSetting, HEARTBEAT_PREFIX + name)
    return datetime.fromisoformat(row.value["at"]) if row else None


def head_revision() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _data_dir() -> Path:
    s = get_settings()
    if s.is_sqlite:
        return Path(s.database_url.removeprefix("sqlite:///")).parent
    return Path(s.backup_dir)


def readiness(db: Session) -> dict[str, Any]:
    """Can this instance serve traffic? (load balancers / Docker health checks)."""
    checks: dict[str, bool] = {}
    try:
        db.execute(text("SELECT 1"))
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["database"] = True
        checks["migrations"] = current == head_revision()
    except Exception:  # noqa: BLE001
        checks["database"] = checks["migrations"] = False
    from app.core.redis import get_redis

    if get_settings().redis_url:
        try:
            r = get_redis()
            checks["redis"] = bool(r and r.ping())
        except Exception:  # noqa: BLE001
            checks["redis"] = False
    path = _data_dir()
    checks["disk"] = path.exists() and shutil.disk_usage(path).free // (1024 * 1024) >= get_settings().min_free_disk_mb
    return {"ready": all(checks.values()), "checks": checks}


def _queues(db: Session, now: datetime) -> dict[str, dict[str, Any]]:
    from app.modules.developer.models import DeliveryStatus, WebhookDelivery
    from app.modules.events.models import OutboxEvent
    from app.modules.integrations.models import MessageStatus, OutboundMessage

    def stat(model: Any, where: Any, ts: Any) -> dict[str, Any]:
        n, oldest = db.execute(select(func.count(), func.min(ts)).where(where)).one()
        age = (now - oldest).total_seconds() if oldest else 0
        return {"pending": int(n), "oldest_seconds": round(age)}

    out = {
        "events_unpublished": stat(OutboxEvent, OutboxEvent.published_at.is_(None), OutboxEvent.created_at),
        "webhooks_undispatched": stat(OutboxEvent, OutboxEvent.webhooks_dispatched_at.is_(None),
                                      OutboxEvent.created_at),
        "messages_queued": stat(OutboundMessage, OutboundMessage.status == MessageStatus.QUEUED,
                                OutboundMessage.created_at),
        "webhook_deliveries": stat(WebhookDelivery, WebhookDelivery.status == DeliveryStatus.PENDING,
                                   WebhookDelivery.created_at),
    }
    out["messages_failed_24h"] = {"count": int(db.scalar(select(func.count()).where(
        OutboundMessage.status == MessageStatus.FAILED, OutboundMessage.updated_at > now - timedelta(days=1))) or 0)}
    out["webhook_deliveries_failed_24h"] = {"count": int(db.scalar(select(func.count()).where(
        WebhookDelivery.status == DeliveryStatus.FAILED, WebhookDelivery.created_at > now - timedelta(days=1))) or 0)}
    return out


def system_health(db: Session) -> dict[str, Any]:
    """Owner dashboard: everything that needs attention, with plain-language warnings."""
    from app.modules.system import backups

    now = datetime.now(UTC)
    ready = readiness(db)
    hb = last_heartbeat(db)
    last_ok = backups.last_success(db)
    last_any = db.scalar(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(1))
    queues = _queues(db, now)
    domains = dict(db.execute(select(CustomDomain.status, func.count()).group_by(CustomDomain.status)).all())
    warnings: list[str] = []
    if not ready["ready"]:
        warnings += [f"{k} check failing" for k, ok in ready["checks"].items() if not ok]
    if hb is None or now - hb > WORKER_STALE:
        warnings.append("The background worker is not running (messages, webhooks and backups are paused)")
    if not get_settings().backup_key:
        warnings.append("Backups are not configured (ERP_BACKUP_KEY)")
    elif last_ok is None or now - last_ok.started_at > BACKUP_STALE:
        warnings.append("No successful backup in the last 26 hours")
    if last_any is not None and last_any.status == BackupStatus.FAILED:
        warnings.append(f"Last backup failed: {last_any.error}")
    for name, q in queues.items():
        if q.get("oldest_seconds", 0) > 900:
            warnings.append(f"Queue {name} has items waiting for over 15 minutes")
    if queues["messages_failed_24h"]["count"]:
        warnings.append(f"{queues['messages_failed_24h']['count']} message(s) failed in the last 24 hours")
    if domains.get("disabled"):
        warnings.append(f"{domains['disabled']} custom domain(s) disabled")
    if get_settings().env == "production":
        warnings += get_settings().check_production()
    path = _data_dir()
    usage = shutil.disk_usage(path) if path.exists() else None
    return {
        "status": "ok" if not warnings else "attention",
        "warnings": warnings,
        "readiness": ready,
        "worker": {"last_heartbeat": hb, "alive": bool(hb and now - hb <= WORKER_STALE)},
        "backups": {
            "configured": bool(get_settings().backup_key),
            "last_success_at": last_ok.started_at if last_ok else None,
            "last_success_age_hours": backups.age_hours(last_ok, now),
            "last_status": last_any.status.value if last_any else None,
        },
        "queues": queues,
        "domains": {k.value if hasattr(k, "value") else k: v for k, v in domains.items()},
        "disk": {"free_mb": usage.free // (1024 * 1024), "total_mb": usage.total // (1024 * 1024)} if usage else None,
        "version": {"schema": head_revision()},
    }


def gauges(db: Session) -> list[tuple[str, str, dict[str, str], float]]:
    from app.modules.system import backups

    now = datetime.now(UTC)
    out: list[tuple[str, str, dict[str, str], float]] = []
    for name, q in _queues(db, now).items():
        if "pending" in q:
            out.append(("erp_queue_pending", "Items waiting in a background queue", {"queue": name}, q["pending"]))
            out.append(("erp_queue_oldest_seconds", "Age of the oldest waiting item", {"queue": name},
                        q["oldest_seconds"]))
        else:
            out.append(("erp_queue_failed_24h", "Items that failed in the last 24 hours", {"queue": name}, q["count"]))
    hb = last_heartbeat(db)
    out.append(("erp_worker_heartbeat_age_seconds", "Seconds since the worker last reported", {},
                (now - hb).total_seconds() if hb else -1))
    ok = backups.last_success(db)
    out.append(("erp_backup_last_success_age_seconds", "Seconds since the last successful backup (-1: never)",
                {}, (now - ok.started_at).total_seconds() if ok else -1))
    out.append(("erp_backup_last_size_bytes", "Size of the last successful backup", {},
                float(ok.size_bytes or 0) if ok else 0))
    ready = readiness(db)
    for k, v in ready["checks"].items():
        out.append(("erp_ready", "Readiness check (1 = passing)", {"check": k}, 1.0 if v else 0.0))
    return out
