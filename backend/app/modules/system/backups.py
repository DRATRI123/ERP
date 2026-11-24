"""Automated, encrypted, verified database backups with GFS retention and restore.

* SQLite: online copy through SQLite's backup API (consistent under WAL while the app runs),
  integrity-checked, gzip-compressed.
* PostgreSQL: ``pg_dump --format=custom`` (consistent snapshot; already compressed).

The payload is encrypted with AES-256-GCM (``ERP_BACKUP_KEY``, see ``backupcrypt``) and only
then written to the backup directory or copied off-site, so a stolen backup is useless without
the key. Every backup is verified straight after it is written: the file is decrypted again, its
SHA-256 compared with the original payload and the payload checked (SQLite ``integrity_check``,
``pg_restore --list``). Restores (``python -m app.cli restore``) go through the same checks.

Keep the backup key somewhere other than the server (password manager + printed copy). Without
it the backups cannot be restored by anyone, including you.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, make_url, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFound, ValidationFailed
from app.modules.system import backupcrypt
from app.modules.system.models import BackupRun, BackupStatus, SystemSetting

log = logging.getLogger("erp.backup")
SETTINGS_KEY = "backup"
DEFAULTS: dict[str, Any] = {
    "enabled": True, "hour_utc": 20,  # 01:30 IST
    "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 12,
    "offsite_integration_id": None,
}
STALE_RUNNING = timedelta(hours=3)


class BackupError(AppError):
    status_code = 503
    code = "backup_failed"


def backup_dir() -> Path:
    d = Path(get_settings().backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key() -> bytes:
    raw = get_settings().backup_key
    if not raw:
        raise BackupError("Set ERP_BACKUP_KEY to enable backups", code="backup_key_missing")
    try:
        return backupcrypt.parse_key(raw)
    except backupcrypt.BackupCryptoError as exc:
        raise BackupError(str(exc), code="backup_key_invalid") from exc


def free_disk_mb(path: Path) -> int:
    return shutil.disk_usage(path).free // (1024 * 1024)


# ---------------------------------------------------------------------------- settings

def get_backup_settings(db: Session) -> dict[str, Any]:
    row = db.get(SystemSetting, SETTINGS_KEY)
    return {**DEFAULTS, **(row.value if row else {})}


def save_backup_settings(db: Session, changes: dict[str, Any]) -> dict[str, Any]:
    current = get_backup_settings(db)
    current.update({k: v for k, v in changes.items() if k in DEFAULTS})
    if not 0 <= int(current["hour_utc"]) <= 23:
        raise ValidationFailed("hour_utc must be 0–23")
    for k in ("keep_daily", "keep_weekly", "keep_monthly"):
        if not 0 <= int(current[k]) <= 400:
            raise ValidationFailed(f"{k} must be 0–400")
    if current["keep_daily"] < 1:
        raise ValidationFailed("keep_daily must be at least 1")
    if current["offsite_integration_id"]:
        from app.modules.integrations.models import Integration

        i = db.get(Integration, uuid.UUID(str(current["offsite_integration_id"])))
        if i is None or i.provider != "google":
            raise ValidationFailed("Off-site copies need a connected Google integration")
        current["offsite_integration_id"] = str(i.id)
    row = db.get(SystemSetting, SETTINGS_KEY)
    if row is None:
        db.add(SystemSetting(key=SETTINGS_KEY, value=current))
    else:
        row.value = current
    return current


# ---------------------------------------------------------------------------- dump / check

def _pg_args(url: str) -> tuple[list[str], dict[str, str]]:
    """libpq connection arguments; the password travels in the environment, not argv."""
    u = make_url(url)
    args = ["-h", u.host or "localhost", "-p", str(u.port or 5432), "-d", u.database or ""]
    if u.username:
        args += ["-U", u.username]
    env = {**os.environ}
    if u.password:
        env["PGPASSWORD"] = u.password
    return args, env


def _run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600, check=False)
    if proc.returncode != 0:
        raise BackupError(f"{Path(cmd[0]).name} failed: {proc.stderr.strip()[-400:]}")


def dump_database(engine: Engine, dest: Path) -> str:
    """Write a consistent dump of the live database to ``dest``. Returns the payload kind."""
    if engine.dialect.name == "sqlite":
        src = engine.url.database
        if not src or src == ":memory:":
            raise BackupError("In-memory databases cannot be backed up")
        raw = dest.with_suffix(".db")
        s, d = sqlite3.connect(src), sqlite3.connect(raw)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
        _check_sqlite(raw)
        with raw.open("rb") as fi, gzip.open(dest, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo, 1024 * 1024)
        raw.unlink()
        return "sqlite.gz"
    if engine.dialect.name == "postgresql":
        args, env = _pg_args(engine.url.render_as_string(hide_password=False))
        _run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(dest),
              *args], env)
        return "pg_dump"
    raise BackupError(f"Backups are not supported for {engine.dialect.name}")


def _check_sqlite(path: Path) -> str | None:
    """Integrity check; returns the schema revision recorded in the copy."""
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupError(f"SQLite integrity check failed: {result}")
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise BackupError(f"SQLite foreign key check failed: {len(bad)} row(s)")
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.DatabaseError:
            row = None
        return row[0] if row else None
    finally:
        conn.close()


def _check_payload(payload: Path, kind: str, work: Path) -> None:
    if kind == "sqlite.gz":
        db = work / "check.db"
        with gzip.open(payload, "rb") as fi, db.open("wb") as fo:
            shutil.copyfileobj(fi, fo, 1024 * 1024)
        _check_sqlite(db)
        db.unlink()
    elif kind == "pg_dump":
        _run(["pg_restore", "--list", str(payload)])
    else:
        raise BackupError(f"Unknown backup payload {kind!r}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_file(path: Path, key: bytes | None = None, *, expected_payload_sha: str | None = None) -> dict[str, Any]:
    """Decrypt and check a backup file. Raises on any problem; returns its header."""
    key = key or _key()
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        work = Path(tmp)
        try:
            header = backupcrypt.decrypt_file(path, work / "payload", key)
        except backupcrypt.BackupCryptoError as exc:
            raise BackupError(str(exc), code="backup_invalid") from exc
        if expected_payload_sha and _sha256(work / "payload") != expected_payload_sha:
            raise BackupError("Decrypted backup does not match what was written")
        _check_payload(work / "payload", header["payload"], work)
    return header


# ---------------------------------------------------------------------------- running backups

def _current_revision(engine: Engine) -> str | None:
    from sqlalchemy import text

    try:
        with engine.connect() as c:
            return c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001
        return None


def create_backup(engine: Engine, *, key: bytes | None = None, dest_dir: Path | None = None) -> tuple[Path, str]:
    """Dump, encrypt and verify. Returns (file, sha256 of the encrypted file)."""
    key = key or _key()
    dest_dir = dest_dir or backup_dir()
    if free_disk_mb(dest_dir) < get_settings().min_free_disk_mb:
        raise BackupError("Not enough free disk space for a backup", code="disk_full")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    final = dest_dir / f"erp-{stamp}-{engine.dialect.name}-{uuid.uuid4().hex[:6]}.erpbk"
    with tempfile.TemporaryDirectory(dir=dest_dir, prefix=".work-") as tmp:
        payload = Path(tmp) / "payload"
        kind = dump_database(engine, payload)
        payload_sha = _sha256(payload)
        part = final.with_suffix(".part")
        backupcrypt.encrypt_file(payload, part, key, {
            "payload": kind, "dialect": engine.dialect.name, "created_at": stamp,
            "revision": _current_revision(engine), "payload_sha256": payload_sha})
        payload.unlink()
        try:
            verify_file(part, key, expected_payload_sha=payload_sha)
        except Exception:
            part.unlink(missing_ok=True)
            raise
        part.rename(final)
    return final, _sha256(final)


def run_backup(*, trigger: str, run_id: uuid.UUID | None = None) -> BackupRun:
    """Take one backup and record it. Uses its own sessions; never raises for backup failures
    (they are recorded on the run), only for programming errors."""
    from app.db.session import SessionLocal, get_engine

    db = SessionLocal()
    try:
        run = db.get(BackupRun, run_id) if run_id else None
        if run is None:
            run = BackupRun(trigger=trigger, status=BackupStatus.RUNNING)
            db.add(run)
        run.status = BackupStatus.RUNNING
        run.started_at = datetime.now(UTC)
        db.commit()
        try:
            path, sha = create_backup(get_engine())
        except Exception as exc:
            log.exception("backup failed")
            run.status = BackupStatus.FAILED
            run.error = str(getattr(exc, "message", exc))[:500]
            run.finished_at = datetime.now(UTC)
            db.commit()
            return run
        run.filename, run.sha256 = path.name, sha
        run.size_bytes = path.stat().st_size
        run.verified = True
        run.status = BackupStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
        db.commit()

        cfg = get_backup_settings(db)
        if cfg.get("offsite_integration_id"):
            try:
                run.offsite = _upload_offsite(db, cfg["offsite_integration_id"], path)
            except Exception as exc:  # noqa: BLE001 - local copy is fine; report the off-site miss
                log.warning("off-site copy failed: %s", exc)
                run.error = f"off-site copy failed: {getattr(exc, 'message', exc)}"[:500]
            db.commit()
        apply_retention(db)
        db.commit()
        return run
    finally:
        db.close()


def _upload_offsite(db: Session, integration_id: str, path: Path) -> str:
    from app.modules.integrations import service as isvc
    from app.modules.integrations.base import CallLog
    from app.modules.integrations.models import Integration
    from app.modules.integrations.providers import commerce

    i = db.get(Integration, uuid.UUID(integration_id))
    if i is None:
        raise BackupError("Off-site integration no longer exists")
    with CallLog(db, i, "drive_backup") as call:
        file_id = commerce.drive_upload_file(i.config, isvc.credentials(i), path=path)
        call.detail = f"{path.name} → {file_id}"
    return f"google_drive:{file_id}"


def queue_backup(db: Session) -> BackupRun:
    if db.scalar(select(BackupRun.id).where(
            BackupRun.status.in_([BackupStatus.QUEUED, BackupStatus.RUNNING]))):
        raise ValidationFailed("A backup is already queued or running", code="backup_in_progress")
    _key()  # fail now, not later in the worker
    run = BackupRun(trigger="manual", status=BackupStatus.QUEUED)
    db.add(run)
    db.flush()
    return run


def _last_slot(now: datetime, hour: int) -> datetime:
    slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return slot if now >= slot else slot - timedelta(days=1)


def run_pending(now: datetime | None = None) -> str | None:
    """Worker: run a queued backup, or the scheduled daily one when it is due."""
    from app.db.session import SessionLocal

    now = now or datetime.now(UTC)
    db = SessionLocal()
    try:
        for stale in db.scalars(select(BackupRun).where(
                BackupRun.status == BackupStatus.RUNNING, BackupRun.started_at < now - STALE_RUNNING)):
            stale.status, stale.error, stale.finished_at = BackupStatus.FAILED, "interrupted", now
        db.commit()
        if db.scalar(select(BackupRun.id).where(BackupRun.status == BackupStatus.RUNNING)):
            return None
        queued = db.scalar(select(BackupRun).where(BackupRun.status == BackupStatus.QUEUED)
                           .order_by(BackupRun.started_at))
        if queued is not None:
            run_id, trigger = queued.id, "manual"
        else:
            cfg = get_backup_settings(db)
            if not cfg["enabled"] or not get_settings().backup_key:
                return None
            slot = _last_slot(now, int(cfg["hour_utc"]))
            recent = db.scalars(select(BackupRun).where(BackupRun.started_at >= slot,
                                                        BackupRun.trigger == "schedule")).all()
            if any(r.status != BackupStatus.FAILED for r in recent):
                return None
            if any(r.started_at > now - timedelta(hours=1) for r in recent):
                return None  # failed within the hour: retry later, not every loop
            run_id, trigger = None, "schedule"
    finally:
        db.close()
    run = run_backup(trigger=trigger, run_id=run_id)
    return run.status.value


# ---------------------------------------------------------------------------- retention

def plan_retention(items: list[tuple[uuid.UUID, datetime]], *, keep_daily: int, keep_weekly: int,
                   keep_monthly: int) -> set[uuid.UUID]:
    """Grandfather-father-son: the newest backup of each of the last ``keep_daily`` days that
    have one, of the last ``keep_weekly`` ISO weeks and of the last ``keep_monthly`` months.
    Returns the ids to keep (always at least the newest)."""
    newest_first = sorted(items, key=lambda x: x[1], reverse=True)
    keep: set[uuid.UUID] = set()

    def bucket(key_fn, limit: int) -> None:  # type: ignore[no-untyped-def]
        seen: list[Any] = []
        for rid, ts in newest_first:
            k = key_fn(ts.date())
            if k in seen:
                continue
            if len(seen) >= limit:
                break
            seen.append(k)
            keep.add(rid)

    bucket(lambda d: d, keep_daily)
    bucket(lambda d: d.isocalendar()[:2], keep_weekly)
    bucket(lambda d: (d.year, d.month), keep_monthly)
    if newest_first:
        keep.add(newest_first[0][0])
    return keep


def apply_retention(db: Session) -> int:
    cfg = get_backup_settings(db)
    runs = db.scalars(select(BackupRun).where(BackupRun.status == BackupStatus.SUCCEEDED,
                                              BackupRun.deleted_at.is_(None))).all()
    keep = plan_retention([(r.id, r.started_at) for r in runs], keep_daily=int(cfg["keep_daily"]),
                          keep_weekly=int(cfg["keep_weekly"]), keep_monthly=int(cfg["keep_monthly"]))
    removed = 0
    for r in runs:
        if r.id in keep:
            continue
        if r.filename:
            (backup_dir() / r.filename).unlink(missing_ok=True)
        r.deleted_at = datetime.now(UTC)
        removed += 1
    return removed


def backup_path(db: Session, run_id: uuid.UUID) -> tuple[BackupRun, Path]:
    run = db.get(BackupRun, run_id)
    if run is None or run.status != BackupStatus.SUCCEEDED or run.deleted_at or not run.filename:
        raise NotFound("Backup not found")
    path = backup_dir() / run.filename
    if not path.is_file():
        raise NotFound("Backup file is missing from the backup directory")
    return run, path


def last_success(db: Session) -> BackupRun | None:
    return db.scalar(select(BackupRun).where(BackupRun.status == BackupStatus.SUCCEEDED)
                     .order_by(BackupRun.started_at.desc()).limit(1))


# ---------------------------------------------------------------------------- restore

@dataclass
class RestoreResult:
    dialect: str
    revision: str | None
    created_at: str
    target: str


def restore(path: Path, target_url: str, *, force: bool = False, key: bytes | None = None) -> RestoreResult:
    """Restore a backup into ``target_url`` (a database URL). The file is decrypted and fully
    verified before anything at the target is touched.

    * SQLite targets are written to a temporary file and moved into place atomically; an
      existing file is only replaced with ``force`` (stop the API and worker first).
    * PostgreSQL targets should be an empty database; with ``force`` existing objects are
      dropped first (``pg_restore --clean``). The restore runs in a single transaction.
    """
    key = key or _key()
    header = backupcrypt.file_header(path)
    target = make_url(target_url)
    target_dialect = target.get_backend_name()
    if target_dialect != header["dialect"]:
        raise BackupError(f"This is a {header['dialect']} backup; restoring it into "
                          f"{target_dialect} is not supported (use the migration utility)")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        payload = work / "payload"
        try:
            backupcrypt.decrypt_file(path, payload, key)
        except backupcrypt.BackupCryptoError as exc:
            raise BackupError(str(exc), code="backup_invalid") from exc
        if header.get("payload_sha256") and _sha256(payload) != header["payload_sha256"]:
            raise BackupError("Backup payload checksum mismatch", code="backup_invalid")
        _check_payload(payload, header["payload"], work)

        if header["payload"] == "sqlite.gz":
            dest = Path(target.database or "")
            if not target.database or target.database == ":memory:":
                raise BackupError("Restore target must be a SQLite file path")
            if dest.exists() and not force:
                raise BackupError(f"{dest} exists; pass --force to replace it (stop the API first)")
            dest.parent.mkdir(parents=True, exist_ok=True)
            staged = dest.with_name(dest.name + ".restoring")
            with gzip.open(payload, "rb") as fi, staged.open("wb") as fo:
                shutil.copyfileobj(fi, fo, 1024 * 1024)
                fo.flush()
                os.fsync(fo.fileno())
            _check_sqlite(staged)
            for suffix in ("-wal", "-shm"):
                Path(f"{dest}{suffix}").unlink(missing_ok=True)
            staged.replace(dest)
        else:
            args, env = _pg_args(target.render_as_string(hide_password=False))
            cmd = ["pg_restore", "--no-owner", "--no-privileges", "--single-transaction",
                   "--exit-on-error"]
            if force:
                cmd += ["--clean", "--if-exists"]
            _run([*cmd, *args, str(payload)], env)
    return RestoreResult(header["dialect"], header.get("revision"), header["created_at"],
                         target.render_as_string(hide_password=True))


def age_hours(run: BackupRun | None, now: datetime | None = None) -> float | None:
    if run is None:
        return None
    return round(((now or datetime.now(UTC)) - run.started_at).total_seconds() / 3600, 2)


__all__ = [
    "BackupError",
    "apply_retention",
    "create_backup",
    "get_backup_settings",
    "plan_retention",
    "queue_backup",
    "restore",
    "run_backup",
    "run_pending",
    "save_backup_settings",
    "verify_file",
]
