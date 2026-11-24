"""Operations CLI: ``python -m app.cli <command>``.

The owner account can only be created here — there is no public registration endpoint.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from app.core.config import BACKEND_DIR, get_settings


def _alembic_config():  # type: ignore[no-untyped-def]
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def cmd_migrate(args: argparse.Namespace) -> int:
    from alembic import command

    command.upgrade(_alembic_config(), args.revision)
    print(f"Database migrated to {args.revision}")
    return 0


def cmd_create_owner(args: argparse.Namespace) -> int:
    from app.core.errors import AppError
    from app.db.session import session_scope
    from app.modules.users.service import create_owner

    password = args.password or getpass.getpass("Owner password: ")
    if not args.password and password != getpass.getpass("Repeat password: "):
        print("Passwords do not match", file=sys.stderr)
        return 1
    try:
        with session_scope() as db:
            owner = create_owner(db, email=args.email, full_name=args.name, password=password)
            print(f"Owner created: {owner.email} ({owner.id})")
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    return 0


def cmd_db_check(_: argparse.Namespace) -> int:
    from app.db.maintenance import check_integrity
    from app.db.session import get_engine

    report = check_integrity(get_engine())
    print(f"{report.dialect}: {'OK' if report.ok else 'PROBLEMS FOUND'}")
    for p in report.problems:
        print(f"  - {p}")
    return 0 if report.ok else 2


def cmd_db_backup(args: argparse.Namespace) -> int:
    from app.db.maintenance import backup_sqlite
    from app.db.session import get_engine

    dest = backup_sqlite(get_engine(), Path(args.dest))
    print(f"Backup written and verified: {dest}")
    return 0


def cmd_accounting_backfill(_: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.modules.accounting.backfill import backfill
    from app.modules.accounting.reports import verify_books
    from app.modules.businesses.models import Business

    status = 0
    with session_scope() as db:
        for business in db.scalars(select(Business).order_by(Business.name)):
            r = backfill(db, business)
            print(f"{business.name}: {r.posted} posted, {r.reversed} reversed, {len(r.skipped)} skipped")
            for s in r.skipped:
                print(f"  - skipped {s}")
            problems = verify_books(db, business.id)
            for p in problems:
                print(f"  ! {p}")
                status = 2
    return status


def cmd_worker(args: argparse.Namespace) -> int:
    """Background jobs: queued e-mails / WhatsApp / SMS, outgoing webhooks, the real-time event
    relay, backups, domain re-verification and security alerts. Run exactly one, under systemd
    or Docker alongside the API."""
    import time
    from datetime import UTC, datetime

    from app.db.session import SessionLocal
    from app.modules.integrations.outbox import process_due

    while True:
        from app.modules.developer.webhooks import deliver_due, emit_overdue, fan_out
        from app.modules.system import backups, domains, monitor, security

        db = SessionLocal()
        stats: dict[str, object] = {}
        try:
            stats = process_due(db)
            stats["webhooks_queued"] = fan_out(db)
            from app.core.redis import get_redis

            if get_redis() is not None:
                from app.modules.events.relay import publish_pending

                try:
                    stats["events_streamed"] = publish_pending(db, get_redis())
                except Exception as exc:  # noqa: BLE001 - Redis down: retried next loop
                    print(f"worker: event relay failed: {exc}", flush=True)
            stats.update({f"webhook_{k}": v for k, v in deliver_due(db).items()})
            if datetime.now(UTC).hour == 3 or args.once:  # daily overdue scan (idempotent)
                stats["overdue_events"] = emit_overdue(db)
            stats.update(security.deliver_alerts(db))
            d = domains.reverify_due(db)
            if d["checked"]:
                stats["domains_checked"] = d["checked"]
            monitor.heartbeat(db, "worker", {k: v for k, v in stats.items() if v})
        except Exception as exc:  # noqa: BLE001 - keep the loop alive; the heartbeat shows health
            db.rollback()
            print(f"worker: loop failed: {exc!r}", flush=True)
        finally:
            db.close()
        backup = backups.run_pending()  # own sessions; long-running
        if backup:
            stats["backup"] = backup
        if any(stats.values()):
            print(f"worker: {stats}", flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


def cmd_backup(_: argparse.Namespace) -> int:
    from app.modules.system.backups import run_backup

    run = run_backup(trigger="cli")
    if run.status.value != "succeeded":
        print(f"Backup FAILED: {run.error}", file=sys.stderr)
        return 2
    print(f"Backup written and verified: {run.filename} ({run.size_bytes} bytes, sha256 {run.sha256})")
    if run.error:
        print(f"Warning: {run.error}", file=sys.stderr)
    return 0


def cmd_verify_backup(args: argparse.Namespace) -> int:
    from app.core.errors import AppError
    from app.modules.system.backups import verify_file

    try:
        header = verify_file(Path(args.file))
    except AppError as exc:
        print(f"INVALID: {exc.message}", file=sys.stderr)
        return 2
    print(f"OK: {header['dialect']} backup from {header['created_at']}, schema {header.get('revision')}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore into ``--target`` (default: ERP_DATABASE_URL). Stop the API and worker first."""
    from app.core.errors import AppError
    from app.modules.system.backups import restore

    target = args.target or get_settings().database_url
    if not args.yes:
        answer = input(f"Restore {args.file} into {target.split('@')[-1]}? Type RESTORE to continue: ")
        if answer.strip() != "RESTORE":
            print("Aborted")
            return 1
    try:
        r = restore(Path(args.file), target, force=args.force)
    except AppError as exc:
        print(f"Restore FAILED: {exc.message}", file=sys.stderr)
        return 2
    print(f"Restored {r.dialect} backup from {r.created_at} (schema {r.revision}) into {r.target}")
    print("Next: python -m app.cli migrate   (brings an older backup up to the current schema)")
    return 0


def cmd_backup_keygen(_: argparse.Namespace) -> int:
    from app.modules.system.backupcrypt import generate_key

    print(generate_key())
    print("Store this key OUTSIDE the server (password manager + printed copy). "
          "Without it, backups cannot be restored.", file=sys.stderr)
    return 0


def cmd_reset_mfa(args: argparse.Namespace) -> int:
    """For the owner (or anyone) locked out of 2FA: needs shell access to the server."""
    from app.db.session import session_scope
    from app.modules.system.security import owner_reset_mfa
    from app.modules.users.service import get_by_email, normalize_email

    with session_scope() as db:
        user = get_by_email(db, normalize_email(args.email))
        if user is None:
            print("No such user", file=sys.stderr)
            return 1
        owner_reset_mfa(db, target=user, actor=user)
    print(f"Two-factor authentication switched off for {args.email}; all sessions signed out")
    return 0


def cmd_check_config(_: argparse.Namespace) -> int:
    problems = get_settings().check_production()
    for p in problems:
        print(f"  - {p}")
    print("Configuration OK for production" if not problems else "Fix the settings above before going live")
    return 1 if problems else 0


def cmd_rotate_encryption(_: argparse.Namespace) -> int:
    """After prepending a new key to ERP_ENCRYPTION_KEYS: re-encrypt every stored secret."""
    from sqlalchemy import select

    from app.core.crypto import reencrypt
    from app.db.session import session_scope
    from app.modules.integrations.models import Integration

    n = 0
    with session_scope() as db:
        for i in db.scalars(select(Integration).where(Integration.credentials_encrypted.is_not(None))):
            i.credentials_encrypted = reencrypt(i.credentials_encrypted)
            n += 1
    print(f"Re-encrypted credentials of {n} integration(s) with the primary key")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="erp")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("migrate", help="Apply database migrations")
    p.add_argument("revision", nargs="?", default="head")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("create-owner", help="Create the single owner account")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--password", help="Omit to be prompted (recommended)")
    p.set_defaults(func=cmd_create_owner)

    p = sub.add_parser("db-check", help="Run database integrity checks")
    p.set_defaults(func=cmd_db_check)

    p = sub.add_parser("backup", help="Encrypted, verified backup into ERP_BACKUP_DIR")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("verify-backup", help="Decrypt and check a backup file")
    p.add_argument("file")
    p.set_defaults(func=cmd_verify_backup)

    p = sub.add_parser("restore", help="Restore an encrypted backup")
    p.add_argument("file")
    p.add_argument("--target", help="Database URL (default: ERP_DATABASE_URL)")
    p.add_argument("--force", action="store_true", help="Replace an existing database")
    p.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("backup-keygen", help="Generate an ERP_BACKUP_KEY")
    p.set_defaults(func=cmd_backup_keygen)

    p = sub.add_parser("reset-mfa", help="Switch off two-factor authentication for a user")
    p.add_argument("--email", required=True)
    p.set_defaults(func=cmd_reset_mfa)

    p = sub.add_parser("check-config", help="Check production settings")
    p.set_defaults(func=cmd_check_config)

    p = sub.add_parser("db-backup", help="Unencrypted SQLite copy (local use; prefer 'backup')")
    p.add_argument("--dest", default=str(BACKEND_DIR / "backups"))
    p.set_defaults(func=cmd_db_backup)

    p = sub.add_parser("accounting-backfill",
                       help="Post journals for earlier invoices, payments and stock documents")
    p.set_defaults(func=cmd_accounting_backfill)

    p = sub.add_parser("worker", help="Run background jobs (loop)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=int, default=15)
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("rotate-encryption", help="Re-encrypt stored secrets with the newest key")
    p.set_defaults(func=cmd_rotate_encryption)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
