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

    p = sub.add_parser("db-backup", help="Online, verified SQLite backup")
    p.add_argument("--dest", default=str(BACKEND_DIR / "backups"))
    p.set_defaults(func=cmd_db_backup)

    p = sub.add_parser("accounting-backfill",
                       help="Post journals for earlier invoices, payments and stock documents")
    p.set_defaults(func=cmd_accounting_backfill)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
